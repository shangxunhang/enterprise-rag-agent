# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：_json_hash、_is_explicit_local_model_path、_write_jsonl、OfflineIndexBuildResult、OfflineIndexBuilder。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Reproducible offline parent/child index builder."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from rag.config.static_retrieval import ComponentConfig
from rag.embed.embedding_service import encode_texts_with_hash, encode_texts_with_model
from rag.schema.VectorIndexRecord_Schema import build_vector_index_record_v2
from rag.offline.config import OfflineIndexBuildConfig, OfflineIndexConfigLoader
from rag.offline.manifest import ActiveIndexPointer, ArtifactRecord, IndexManifest, milvus_semantic_fingerprint, sha256_file
from rag.registry.default_registrations import build_default_component_registry
from rag.store.parent_chunk_store import load_jsonl_dicts


# 阅读注释（函数）：处理 JSON hash 相关逻辑。
def _json_hash(payload: Any) -> str:
    """处理 JSON hash 相关逻辑。

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




# 阅读注释（函数）：判断 explicit 本地 模型 路径。
def _is_explicit_local_model_path(value: str) -> bool:
    """判断 explicit 本地 模型 路径。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        bool

    阅读提示:
        主要直接调用：strip, str, bool, re.match, normalized.startswith。
    """
    normalized = str(value or "").strip()
    return bool(
        re.match(r"^[A-Za-z]:[\\/]", normalized)
        or normalized.startswith(("/", "\\\\", "./", "../", ".\\", "..\\"))
    )

# 阅读注释（函数）：写入 jsonl。
def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """写入 jsonl。

    参数:
        path: 目标文件或目录路径。
        records: 记录集合，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：path.parent.mkdir, path.open, handle.write, json.dumps。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            handle.write("\n")


# 阅读注释（类）：封装 离线 索引 build 结果，集中封装相关状态、依赖和行为。
@dataclass(frozen=True)
class OfflineIndexBuildResult:
    """封装 离线 索引 build 结果，集中封装相关状态、依赖和行为。"""
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

    # 阅读注释（函数）：把 OfflineIndexBuildResult 转换为 字典。
    def to_dict(self) -> dict[str, Any]:
        """把 OfflineIndexBuildResult 转换为 字典。

        返回:
            dict[str, Any]

        阅读提示:
            主要直接调用：self.__dict__.copy。
        """
        return self.__dict__.copy()


# 阅读注释（类）：封装 离线 索引 builder，集中封装相关状态、依赖和行为。
class OfflineIndexBuilder:
    """封装 离线 索引 builder，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 OfflineIndexBuilder，保存运行所需的依赖、配置或状态。
    def __init__(self, *, registry=None, project_root: str | Path | None = None) -> None:
        """初始化 OfflineIndexBuilder，保存运行所需的依赖、配置或状态。

        参数:
            registry: 注册表，具体约束请结合类型标注和调用方确认。
            project_root: 项目 root，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：resolve, expanduser, Path, OfflineIndexConfigLoader.default_project_root, build_default_component_registry。
        """
        self.project_root = (
            Path(project_root).expanduser().resolve()
            if project_root
            else OfflineIndexConfigLoader.default_project_root()
        )
        self.registry = registry or build_default_component_registry()

    # 阅读注释（函数）：解析并确定 OfflineIndexBuilder。
    def resolve(self, path: str | Path) -> Path:
        """解析并确定 OfflineIndexBuilder。

        参数:
            path: 目标文件或目录路径。

        返回:
            Path

        阅读提示:
            主要直接调用：expanduser, Path, candidate.is_absolute, candidate.resolve, resolve。
        """
        candidate = Path(path).expanduser()
        return candidate.resolve() if candidate.is_absolute() else (self.project_root / candidate).resolve()

    # 阅读注释（函数）：校验 OfflineIndexBuilder。
    def validate(self, config: OfflineIndexBuildConfig) -> OfflineIndexBuildResult:
        """校验 OfflineIndexBuilder。

        参数:
            config: 运行配置。

        返回:
            OfflineIndexBuildResult

        阅读提示:
            主要直接调用：self.resolve, source_path.exists, FileNotFoundError, strip, str, _is_explicit_local_model_path, exists, expanduser。
        """
        source_path = self.resolve(config.source.path)
        if not source_path.exists():
            raise FileNotFoundError(f"cleaned unit source not found: {source_path}")
        if config.embedding.mode == "model":
            model_name = str(config.embedding.model_name or "").strip()
            if _is_explicit_local_model_path(model_name) and not Path(model_name).expanduser().exists():
                raise FileNotFoundError(f"embedding model path not found: {model_name}")
        if not self.registry.contains(
            category="chunker",
            name=config.chunker.name,
            version=config.chunker.version,
        ):
            available = [
                f"{item.name}@{item.version}"
                for item in self.registry.list_components(category="chunker")
            ]
            raise ValueError(
                f"unknown chunker {config.chunker.name}@{config.chunker.version}; available={available}"
            )
        index_version = config.index_version()
        output_dir = self.resolve(config.outputs.root_dir) / index_version
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

    # 阅读注释（函数）：构建 OfflineIndexBuilder。
    def build(self, config: OfflineIndexBuildConfig) -> OfflineIndexBuildResult:
        """构建 OfflineIndexBuilder。

        参数:
            config: 运行配置。

        返回:
            OfflineIndexBuildResult

        阅读提示:
            主要直接调用：self.validate, self.resolve, Path, manifest_path.exists, IndexManifest.model_validate_json, manifest_path.read_text, config.config_hash, ValueError。
        """
        validated = self.validate(config)
        source_path = self.resolve(config.source.path)
        output_dir = Path(validated.output_dir)
        manifest_path = output_dir / "index_manifest.json"

        # Immutable build directory. An identical completed build is idempotent;
        # a partial or conflicting directory must be handled explicitly.
        if manifest_path.exists():
            existing = IndexManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
            if existing.config_hash != config.config_hash() or existing.source_hash != validated.source_hash:
                raise ValueError(f"index version collision: {manifest_path}")
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
        if output_dir.exists() and any(output_dir.iterdir()):
            raise ValueError(f"incomplete non-empty index directory exists: {output_dir}")
        output_dir.mkdir(parents=True, exist_ok=True)

        records = load_jsonl_dicts(source_path)
        component_params = dict(config.chunker.params)
        component_params.setdefault("deterministic_created_at", config.deterministic_created_at)
        chunker_config = ComponentConfig(
            name=config.chunker.name,
            version=config.chunker.version,
            enabled=True,
            params=component_params,
        )
        chunker = self.registry.build(category="chunker", config=chunker_config)
        chunk_result = chunker.chunk_records(records)
        parents = sorted(chunk_result.parents, key=lambda item: str(item.get("parent_chunk_id") or ""))
        children = sorted(chunk_result.children, key=lambda item: str(item.get("child_chunk_id") or ""))

        index_version = validated.index_version
        for record in [*parents, *children]:
            extra = dict(record.get("extra") or {})
            extra.update(
                {
                    "offline_index_version": index_version,
                    "offline_index_config_hash": config.config_hash(),
                    "dataset_version": config.dataset_version,
                }
            )
            record["extra"] = extra

        parent_path = output_dir / "parent_chunks.jsonl"
        child_path = output_dir / "child_chunks.jsonl"
        _write_jsonl(parent_path, parents)
        _write_jsonl(child_path, children)

        texts = [str(item.get("text") or "") for item in children]
        if not texts:
            raise ValueError("chunker produced zero child chunks")
        if config.embedding.mode == "hash":
            vectors, embedding_model, embedding_version = encode_texts_with_hash(texts, dim=config.embedding.dim)
        else:
            vectors, embedding_model, embedding_version = encode_texts_with_model(
                texts,
                model_name=config.embedding.model_name,
                device=config.embedding.device,
                batch_size=config.embedding.batch_size,
                embedding_version=config.embedding.version,
            )
        if vectors.ndim != 2 or int(vectors.shape[0]) != len(texts):
            raise ValueError(
                "embedding output must be a 2D matrix with one row per child chunk; "
                f"got shape={getattr(vectors, 'shape', None)} child_count={len(texts)}"
            )
        embedding_dim = int(vectors.shape[1])
        if embedding_dim != int(config.embedding.dim):
            raise ValueError(
                "embedding dimension mismatch: "
                f"configured={config.embedding.dim} actual={embedding_dim} "
                f"model={embedding_model}"
            )
        vectors_path = output_dir / "child_vectors.npy"
        np.save(vectors_path, vectors.astype("float32"), allow_pickle=False)

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
                    "offline_index_config_hash": config.config_hash(),
                },
            )
            for child in children
        ]
        vector_records_path = output_dir / "vector_index_records.jsonl"
        _write_jsonl(vector_records_path, vector_records)

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
                        f"expected={len(children)} actual={inserted_count}"
                    )
            finally:
                # Do not call MilvusClient.flush() here.  Some Milvus Lite
                # Windows releases implement manifest replacement with
                # os.rename(), which cannot replace an existing file and raises
                # WinError 183.  Closing and reopening the embedded database,
                # followed by an entity-count check, gives us a portable and
                # stronger persistence boundary.
                close = getattr(client, "close", None)
                if callable(close):
                    close()

            verification_client = MilvusClient(str(milvus_path))
            try:
                has_collection = getattr(verification_client, "has_collection", None)
                if callable(has_collection) and not has_collection(config.index.collection_name):
                    raise ValueError(
                        "Milvus collection missing after reopen: "
                        f"{config.index.collection_name}"
                    )
                stats = verification_client.get_collection_stats(
                    collection_name=config.index.collection_name
                )
                persisted_count = int((stats or {}).get("row_count", -1))
                if persisted_count != len(children):
                    raise ValueError(
                        "Milvus persisted entity count mismatch after reopen: "
                        f"expected={len(children)} actual={persisted_count}"
                    )
            finally:
                close = getattr(verification_client, "close", None)
                if callable(close):
                    close()

        artifacts: dict[str, ArtifactRecord] = {
            "parent_chunks": ArtifactRecord(path=str(parent_path), sha256=sha256_file(parent_path), record_count=len(parents)),
            "child_chunks": ArtifactRecord(path=str(child_path), sha256=sha256_file(child_path), record_count=len(children)),
            "vectors": ArtifactRecord(path=str(vectors_path), sha256=sha256_file(vectors_path), record_count=len(children)),
            "vector_index_records": ArtifactRecord(
                path=str(vector_records_path), sha256=sha256_file(vector_records_path), record_count=len(vector_records)
            ),
        }
        if milvus_path is not None and milvus_path.exists():
            artifacts["milvus_lite"] = ArtifactRecord(
                path=str(milvus_path),
                sha256=milvus_semantic_fingerprint(
                    collection_name=config.index.collection_name,
                    record_count=len(children),
                    embedding_dim=config.embedding.dim,
                    metric_type=config.index.metric_type,
                    child_chunks_sha256=artifacts["child_chunks"].sha256,
                    vector_records_sha256=artifacts["vector_index_records"].sha256,
                ),
                record_count=len(children),
                kind="directory" if milvus_path.is_dir() else "file",
                integrity_mode="milvus_semantic_v1",
            )

        reproducibility_payload = {
            "config_hash": config.config_hash(),
            "source_hash": validated.source_hash,
            "artifacts": {name: item.sha256 for name, item in sorted(artifacts.items())},
        }
        plugin_metadata = getattr(chunker, "plugin_metadata", None)
        manifest = IndexManifest(
            index_version=index_version,
            build_id=config.build_id,
            dataset_version=config.dataset_version,
            config_hash=config.config_hash(),
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
                "implementation": plugin_metadata.implementation if plugin_metadata else chunker.__class__.__name__,
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
            document_count=len({str(item.get("doc_id")) for item in records if item.get("doc_id")}),
            parent_chunk_count=len(parents),
            child_chunk_count=len(children),
            artifacts=artifacts,
            created_at=config.deterministic_created_at,
            reproducibility_hash=_json_hash(reproducibility_payload),
            notes=config.notes,
        )
        manifest.write(manifest_path)

        if config.outputs.update_active_pointer:
            pointer_path = self.resolve(config.outputs.active_pointer)
            ActiveIndexPointer(
                index_version=index_version,
                manifest_path=str(manifest_path),
                manifest_sha256=sha256_file(manifest_path),
            ).write(pointer_path)

        return OfflineIndexBuildResult(
            status="success",
            index_version=index_version,
            config_hash=config.config_hash(),
            source_hash=validated.source_hash,
            output_dir=str(output_dir),
            manifest_path=str(manifest_path),
            document_count=manifest.document_count,
            parent_chunk_count=len(parents),
            child_chunk_count=len(children),
        )
