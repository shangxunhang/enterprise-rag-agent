"""构建可重复、版本化且不自动激活的父子块离线索引。

Builder 负责：验证源数据和组件配置、把 cleaned-text-unit 迁移到 canonical 协议、
补充检索作用域、调用注册 Chunker、编码子块向量、写出不可变 JSONL/NumPy 产物、
可选创建 Milvus Lite 集合，并最终发布 ``IndexManifest``。相同配置和源哈希的已完成
版本可幂等复用；版本目录冲突或半成品必须显式处理。Builder 永远不修改 active pointer。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from rag.config.static_retrieval import ComponentConfig
from rag.embed.embedding_service import (
    encode_texts_with_hash,
    encode_texts_with_model,
)
from rag.offline.config import (
    OfflineIndexBuildConfig,
    OfflineIndexConfigLoader,
)
from rag.offline.manifest import (
    ArtifactRecord,
    IndexManifest,
    milvus_semantic_fingerprint,
    sha256_file,
)
from rag.registry.default_registrations import (
    build_default_component_registry,
)
from rag.schema.VectorIndexRecord_Schema import (
    build_vector_index_record_v2,
)
from rag.schema.cleaned_text_unit import (
    CleanedTextUnitSchema,
    migrate_legacy_cleaned_text_unit_payload,
)
from rag.store.parent_chunk_store import load_jsonl_dicts


def _json_hash(payload: Any) -> str:
    """对任意 JSON 兼容对象的 canonical 表示计算 SHA-256。"""

    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _is_explicit_local_model_path(value: str) -> bool:
    """判断模型字符串是否明确表达本地文件系统路径。

    Windows 盘符、Unix 绝对路径、UNC 路径和 ``./``、``../`` 相对路径都视为本地
    路径；普通 ``organization/model`` Hugging Face 标识不视为本地路径。
    """

    normalized = str(value or "").strip()
    return bool(
        re.match(r"^[A-Za-z]:[\\/]", normalized)
        or normalized.startswith(
            ("/", "\\\\", "./", "../", ".\\", "..\\")
        )
    )


def _write_jsonl(
    path: Path,
    records: list[dict[str, Any]],
) -> None:
    """以确定性键顺序覆盖写入紧凑 JSONL 文件。

    Args:
        path: 目标文件。
        records: 已按稳定业务身份排序的记录列表。

    本函数不是追加式或原子发布。版本目录在构建前必须为空，因此覆盖写入只发生在
    尚未发布的私有构建目录中；最终完成边界由 Manifest 发布表示。
    """

    # 第一步：创建产物父目录。
    path.parent.mkdir(parents=True, exist_ok=True)

    # 第二步：每条记录使用排序键和紧凑分隔符写成一行。
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            handle.write("\n")


@dataclass(frozen=True)
class OfflineIndexBuildResult:
    """描述离线索引验证或构建的结构化结果。

    Attributes:
        status: ``success`` 或幂等复用时的 ``already_exists``。
        index_version: 由索引兼容性身份产生的版本。
        config_hash: 完整构建配置哈希。
        source_hash: 源数据文件内容哈希。
        output_dir: 版本化产物目录。
        manifest_path: 完成构建后的 Manifest 路径；仅验证时为 ``None``。
        document_count: 去重文档数量。
        parent_chunk_count: 父块数量。
        child_chunk_count: 子块和向量数量。
        validation_only: 是否只完成了前置验证。
    """

    status: str
    index_version: str
    config_hash: str
    source_hash: str
    output_dir: str
    manifest_path: str | None
    document_count: int
    parent_chunk_count: int
    child_chunk_count: int
    validation_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        """返回 dataclass 字段的浅拷贝字典。"""

        return self.__dict__.copy()


class OfflineIndexBuilder:
    """协调离线数据迁移、Chunk、Embedding、存储和 Manifest 发布。

    Attributes:
        project_root: 相对路径统一解析基准。
        registry: 已注册 Chunker 等组件的事实源。
    """

    def __init__(
        self,
        *,
        registry=None,
        project_root: str | Path | None = None,
    ) -> None:
        """初始化项目根目录和组件注册表。

        Args:
            registry: 可选自定义组件注册表，测试和插件环境可注入。
            project_root: 可选项目根目录；省略时使用配置加载器推导的仓库根。
        """

        # 第一步：固定绝对项目根目录，后续所有配置相对路径都基于该目录。
        self.project_root = (
            Path(project_root).expanduser().resolve()
            if project_root
            else OfflineIndexConfigLoader.default_project_root()
        )

        # 第二步：未注入注册表时构建项目默认插件注册表。
        self.registry = registry or build_default_component_registry()

    def resolve(self, path: str | Path) -> Path:
        """把绝对或项目相对路径解析为规范化绝对路径。"""

        candidate = Path(path).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        return (self.project_root / candidate).resolve()

    def validate(
        self,
        config: OfflineIndexBuildConfig,
    ) -> OfflineIndexBuildResult:
        """验证源、模型路径和 Chunker 注册，并推导构建身份。

        Args:
            config: 严格离线索引构建配置。

        Returns:
            ``validation_only=True`` 的结果，不创建版本目录或加载模型。

        Raises:
            FileNotFoundError: 源数据或明确本地模型路径不存在时抛出。
            ValueError: Chunker 未注册时抛出，并列出可用版本。
        """

        # 第一步：解析并检查 cleaned-text-unit 源文件。
        source_path = self.resolve(config.source.path)
        if not source_path.exists():
            raise FileNotFoundError(
                f"cleaned unit source not found: {source_path}"
            )

        # 第二步：真实模型模式中，仅对明确本地路径做前置存在性检查。
        if config.embedding.mode == "model":
            model_name = str(
                config.embedding.model_name or ""
            ).strip()
            if (
                _is_explicit_local_model_path(model_name)
                and not Path(model_name).expanduser().exists()
            ):
                raise FileNotFoundError(
                    f"embedding model path not found: {model_name}"
                )

        # 第三步：Chunker 必须以名称和版本精确注册，不能隐式回退其他实现。
        if not self.registry.contains(
            category="chunker",
            name=config.chunker.name,
            version=config.chunker.version,
        ):
            available = [
                f"{item.name}@{item.version}"
                for item in self.registry.list_components(
                    category="chunker"
                )
            ]
            raise ValueError(
                f"unknown chunker "
                f"{config.chunker.name}@{config.chunker.version}; "
                f"available={available}"
            )

        # 第四步：推导索引版本、版本目录和源内容哈希，不产生写入副作用。
        index_version = config.index_version()
        output_dir = (
            self.resolve(config.outputs.root_dir) / index_version
        )
        return OfflineIndexBuildResult(
            status="success",
            index_version=index_version,
            config_hash=config.config_hash(),
            source_hash=sha256_file(source_path),
            output_dir=str(output_dir),
            manifest_path=None,
            document_count=0,
            parent_chunk_count=0,
            child_chunk_count=0,
            validation_only=True,
        )

    def build(
        self,
        config: OfflineIndexBuildConfig,
    ) -> OfflineIndexBuildResult:
        """构建一个不可变版本化父子块索引。

        Args:
            config: 严格离线索引配置。

        Returns:
            新构建或幂等复用的 ``OfflineIndexBuildResult``。

        Raises:
            ValueError: 版本碰撞、半成品目录、作用域冲突、空子块、向量形状/维度错误
                或 Milvus 持久化行数不一致时抛出。
        """

        # 第一步：执行无副作用前置验证并确定版本目录、源哈希和 Manifest 路径。
        validated = self.validate(config)
        source_path = self.resolve(config.source.path)
        output_dir = Path(validated.output_dir)
        manifest_path = output_dir / "index_manifest.json"

        # 第二步：完成版本目录若存在 Manifest，只允许相同配置和相同源内容幂等复用。
        if manifest_path.exists():
            existing = IndexManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
            if (
                existing.config_hash != config.config_hash()
                or existing.source_hash != validated.source_hash
            ):
                raise ValueError(
                    f"index version collision: {manifest_path}"
                )
            return OfflineIndexBuildResult(
                status="already_exists",
                index_version=existing.index_version,
                config_hash=existing.config_hash,
                source_hash=existing.source_hash,
                output_dir=str(output_dir),
                manifest_path=str(manifest_path),
                document_count=existing.document_count,
                parent_chunk_count=existing.parent_chunk_count,
                child_chunk_count=existing.child_chunk_count,
            )

        # 第三步：没有 Manifest 但目录非空表示未完成或冲突构建，禁止覆盖继续。
        if output_dir.exists() and any(output_dir.iterdir()):
            raise ValueError(
                f"incomplete non-empty index directory exists: "
                f"{output_dir}"
            )
        output_dir.mkdir(parents=True, exist_ok=True)

        # 第四步：读取 JSONL 源记录，显式迁移旧 cleaned-text-unit 后再进入 canonical 边界。
        source_records = load_jsonl_dicts(source_path)
        records: list[dict[str, Any]] = []
        for source_record in source_records:
            migrated = migrate_legacy_cleaned_text_unit_payload(
                source_record
            )
            CleanedTextUnitSchema.model_validate(migrated)
            records.append(migrated)

        # 第五步：canonical 校验后补充 RAG 检索作用域；记录级值优先于数据集默认值。
        doc_scopes: dict[str, tuple[str, str]] = {}
        for record in records:
            record["tenant_id"] = (
                record.get("tenant_id")
                or config.source.tenant_id
                or "default"
            )
            record["kb_id"] = (
                record.get("kb_id")
                or config.source.kb_id
                or "default"
            )
            record["file_id"] = (
                record.get("file_id")
                or record.get("source_name")
                or record.get("doc_id")
                or ""
            )

            # 同一物理索引中的 doc_id 必须全局唯一，不能跨租户/知识库复用。
            doc_id = str(record.get("doc_id") or "").strip()
            if doc_id:
                scope = (
                    record["tenant_id"],
                    record["kb_id"],
                )
                previous_scope = doc_scopes.setdefault(
                    doc_id,
                    scope,
                )
                if previous_scope != scope:
                    raise ValueError(
                        "doc_id must be globally unique inside one "
                        "physical index; "
                        f"doc_id={doc_id!r} appears in scopes "
                        f"{previous_scope!r} and {scope!r}"
                    )

        # 第六步：把确定性 created_at 注入 Chunker 参数，并通过注册表创建精确版本实现。
        component_params = dict(config.chunker.params)
        component_params.setdefault(
            "deterministic_created_at",
            config.deterministic_created_at,
        )
        chunker_config = ComponentConfig(
            name=config.chunker.name,
            version=config.chunker.version,
            enabled=True,
            params=component_params,
        )
        chunker = self.registry.build(
            category="chunker",
            config=chunker_config,
        )
        chunk_result = chunker.chunk_records(records)

        # 第七步：按稳定块 ID 排序，使 JSONL 行、向量行和后续哈希跨运行一致。
        parents = sorted(
            chunk_result.parents,
            key=lambda item: str(
                item.get("parent_chunk_id") or ""
            ),
        )
        children = sorted(
            chunk_result.children,
            key=lambda item: str(
                item.get("child_chunk_id") or ""
            ),
        )

        # 第八步：在所有父子块 extra 中固化索引版本、完整配置哈希和数据集版本。
        index_version = validated.index_version
        current_config_hash = config.config_hash()
        for record in [*parents, *children]:
            extra = dict(record.get("extra") or {})
            extra.update(
                {
                    "offline_index_version": index_version,
                    "offline_index_config_hash": current_config_hash,
                    "dataset_version": config.dataset_version,
                }
            )
            record["extra"] = extra

        # 第九步：先发布父块和子块不可变 JSONL 产物。
        parent_path = output_dir / "parent_chunks.jsonl"
        child_path = output_dir / "child_chunks.jsonl"
        _write_jsonl(parent_path, parents)
        _write_jsonl(child_path, children)

        # 第十步：对子块正文编码；零子块表示源或 Chunker 配置无效，禁止生成空索引。
        texts = [str(item.get("text") or "") for item in children]
        if not texts:
            raise ValueError("chunker produced zero child chunks")

        if config.embedding.mode == "hash":
            vectors, embedding_model, embedding_version = (
                encode_texts_with_hash(
                    texts,
                    dim=config.embedding.dim,
                )
            )
        else:
            vectors, embedding_model, embedding_version = (
                encode_texts_with_model(
                    texts,
                    model_name=config.embedding.model_name,
                    device=config.embedding.device,
                    batch_size=config.embedding.batch_size,
                    embedding_version=config.embedding.version,
                )
            )

        # 第十一步：验证向量矩阵一行对应一个子块，且实际维度与配置完全一致。
        if (
            vectors.ndim != 2
            or int(vectors.shape[0]) != len(texts)
        ):
            raise ValueError(
                "embedding output must be a 2D matrix with one "
                "row per child chunk; "
                f"got shape={getattr(vectors, 'shape', None)} "
                f"child_count={len(texts)}"
            )
        embedding_dim = int(vectors.shape[1])
        if embedding_dim != int(config.embedding.dim):
            raise ValueError(
                "embedding dimension mismatch: "
                f"configured={config.embedding.dim} "
                f"actual={embedding_dim} model={embedding_model}"
            )

        # 第十二步：以 float32 NumPy 文件保存向量，并禁止 pickle 对象。
        vectors_path = output_dir / "child_vectors.npy"
        np.save(
            vectors_path,
            vectors.astype("float32"),
            allow_pickle=False,
        )

        # 第十三步：构造每个子块的逻辑 VectorIndexRecord，固化模型和索引血缘。
        index_name = f"{index_version}_children"
        vector_records = [
            build_vector_index_record_v2(
                child_chunk=child,
                embedding_model=embedding_model,
                embedding_dim=embedding_dim,
                embedding_version=embedding_version,
                index_name=index_name,
                index_version=index_version,
                vector_db=config.index.backend,
                created_at=config.deterministic_created_at,
                extra={
                    "dataset_version": config.dataset_version,
                    "offline_index_config_hash": current_config_hash,
                },
            )
            for child in children
        ]
        vector_records_path = (
            output_dir / "vector_index_records.jsonl"
        )
        _write_jsonl(vector_records_path, vector_records)

        # 第十四步：可选创建 Milvus Lite 在线存储，并在关闭重开后校验持久化行数。
        milvus_path: Path | None = None
        if config.index.backend == "milvus_lite":
            from pymilvus import MilvusClient
            from rag.vector_store.milvus_child_chunk_store import (
                build_milvus_child_chunk_record,
                create_or_reset_child_chunk_collection,
                insert_child_chunk_records,
            )

            milvus_path = output_dir / "milvus.db"
            client = MilvusClient(str(milvus_path))
            create_or_reset_child_chunk_collection(
                client=client,
                collection_name=config.index.collection_name,
                dim=embedding_dim,
                metric_type=config.index.metric_type,
                recreate=config.index.recreate,
                max_text_chars=config.index.max_text_chars,
            )

            try:
                # 将每个 canonical 子块与同一行向量映射为物理 Milvus 记录。
                physical_records = [
                    build_milvus_child_chunk_record(
                        child_chunk=child,
                        vector=vector,
                        embedding_model=embedding_model,
                        embedding_dim=embedding_dim,
                        embedding_version=embedding_version,
                        max_text_chars=config.index.max_text_chars,
                        index_name=index_name,
                        index_version=index_version,
                    )
                    for child, vector in zip(children, vectors)
                ]
                inserted_count = insert_child_chunk_records(
                    client=client,
                    collection_name=config.index.collection_name,
                    records=physical_records,
                    batch_size=config.index.insert_batch_size,
                )
                if inserted_count != len(children):
                    raise ValueError(
                        "Milvus insert count mismatch: "
                        f"expected={len(children)} "
                        f"actual={inserted_count}"
                    )
            finally:
                # 不显式 flush：部分 Windows Milvus Lite 版本用 os.rename 替换已存在
                # Manifest 会触发 WinError 183。关闭并重开后核对实体数是更强持久化边界。
                close = getattr(client, "close", None)
                if callable(close):
                    close()

            # 重新打开数据库，确认集合和全部实体已经真正持久化。
            verification_client = MilvusClient(str(milvus_path))
            try:
                has_collection = getattr(
                    verification_client,
                    "has_collection",
                    None,
                )
                if (
                    callable(has_collection)
                    and not has_collection(
                        config.index.collection_name
                    )
                ):
                    raise ValueError(
                        "Milvus collection missing after reopen: "
                        f"{config.index.collection_name}"
                    )

                stats = verification_client.get_collection_stats(
                    collection_name=config.index.collection_name
                )
                persisted_count = int(
                    (stats or {}).get("row_count", -1)
                )
                if persisted_count != len(children):
                    raise ValueError(
                        "Milvus persisted entity count mismatch "
                        "after reopen: "
                        f"expected={len(children)} "
                        f"actual={persisted_count}"
                    )
            finally:
                close = getattr(
                    verification_client,
                    "close",
                    None,
                )
                if callable(close):
                    close()

        # 第十五步：为所有不可变产物计算内容哈希和记录数量。
        artifacts: dict[str, ArtifactRecord] = {
            "parent_chunks": ArtifactRecord(
                path=str(parent_path),
                sha256=sha256_file(parent_path),
                record_count=len(parents),
            ),
            "child_chunks": ArtifactRecord(
                path=str(child_path),
                sha256=sha256_file(child_path),
                record_count=len(children),
            ),
            "vectors": ArtifactRecord(
                path=str(vectors_path),
                sha256=sha256_file(vectors_path),
                record_count=len(children),
            ),
            "vector_index_records": ArtifactRecord(
                path=str(vector_records_path),
                sha256=sha256_file(vector_records_path),
                record_count=len(vector_records),
            ),
        }

        # 第十六步：Milvus 使用逻辑语义指纹，不依赖会被运行时改写的数据库字节。
        if milvus_path is not None and milvus_path.exists():
            artifacts["milvus_lite"] = ArtifactRecord(
                path=str(milvus_path),
                sha256=milvus_semantic_fingerprint(
                    collection_name=config.index.collection_name,
                    record_count=len(children),
                    embedding_dim=config.embedding.dim,
                    metric_type=config.index.metric_type,
                    child_chunks_sha256=(
                        artifacts["child_chunks"].sha256
                    ),
                    vector_records_sha256=(
                        artifacts[
                            "vector_index_records"
                        ].sha256
                    ),
                ),
                record_count=len(children),
                kind=(
                    "directory" if milvus_path.is_dir() else "file"
                ),
                integrity_mode="milvus_semantic_v1",
            )

        # 第十七步：综合配置、源和全部产物哈希形成构建可重复性指纹。
        reproducibility_payload = {
            "config_hash": current_config_hash,
            "source_hash": validated.source_hash,
            "artifacts": {
                name: item.sha256
                for name, item in sorted(artifacts.items())
            },
        }

        # 第十八步：固化组件实现、执行 metadata、模型协议、计数和产物清单。
        plugin_metadata = getattr(chunker, "plugin_metadata", None)
        manifest = IndexManifest(
            index_version=index_version,
            build_id=config.build_id,
            dataset_version=config.dataset_version,
            config_hash=current_config_hash,
            source_hash=validated.source_hash,
            source={
                "path": str(source_path),
                "format": config.source.format,
                "sha256": validated.source_hash,
                "record_count": len(records),
            },
            chunker={
                "name": config.chunker.name,
                "version": config.chunker.version,
                "params": component_params,
                "implementation": (
                    plugin_metadata.implementation
                    if plugin_metadata
                    else chunker.__class__.__name__
                ),
                "execution": chunker.execution_metadata(),
            },
            embedding={
                "mode": config.embedding.mode,
                "name": config.embedding.name,
                "model": embedding_model,
                "version": embedding_version,
                "dim": embedding_dim,
                "device": config.embedding.device,
                "batch_size": config.embedding.batch_size,
                "normalize_embeddings": True,
            },
            index={
                **config.index.model_dump(mode="json"),
                "index_name": index_name,
                "output_dir": str(output_dir),
            },
            document_count=len(
                {
                    str(item.get("doc_id"))
                    for item in records
                    if item.get("doc_id")
                }
            ),
            parent_chunk_count=len(parents),
            child_chunk_count=len(children),
            artifacts=artifacts,
            created_at=config.deterministic_created_at,
            reproducibility_hash=_json_hash(
                reproducibility_payload
            ),
            notes=config.notes,
        )

        # 第十九步：Manifest 原子发布表示该版本构建完成；此前目录都视为未完成私有产物。
        manifest.write(manifest_path)

        # 第二十步：返回构建结果，但不更新 active pointer。
        return OfflineIndexBuildResult(
            status="success",
            index_version=index_version,
            config_hash=current_config_hash,
            source_hash=validated.source_hash,
            output_dir=str(output_dir),
            manifest_path=str(manifest_path),
            document_count=manifest.document_count,
            parent_chunk_count=len(parents),
            child_chunk_count=len(children),
        )
