# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：atomic_write_text、sha256_file、_update_directory_file_fingerprint、fingerprint_path、sha256_path、milvus_semantic_fingerprint、effective_artifact_integrity_mode、ArtifactRecord、IndexManifest、ActiveIndexPointer。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Immutable index manifest and active-index pointer."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# 阅读注释（函数）：处理 atomic write 文本 相关逻辑。
def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace a UTF-8 text file in the same directory."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


# 阅读注释（函数）：处理 sha256 文件 相关逻辑。
def sha256_file(path: Path) -> str:
    """处理 sha256 文件 相关逻辑。

    参数:
        path: 目标文件或目录路径。

    返回:
        str

    阅读提示:
        主要直接调用：hashlib.sha256, path.open, iter, digest.update, digest.hexdigest。
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# 阅读注释（函数）：更新 directory 文件 fingerprint。
def _update_directory_file_fingerprint(
    *,
    digest: "hashlib._Hash",
    root: Path,
    item: Path,
    metadata_only: bool,
    content_sha256: str | None = None,
) -> None:
    """更新 directory 文件 fingerprint。

    参数:
        digest: digest，具体约束请结合类型标注和调用方确认。
        root: root，具体约束请结合类型标注和调用方确认。
        item: 数据项，具体约束请结合类型标注和调用方确认。
        metadata_only: 元数据 only，具体约束请结合类型标注和调用方确认。
        content_sha256: content sha256，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：as_posix, item.relative_to, relative_text.encode, int, item.stat, digest.update, to_bytes, len。
    """
    relative_text = item.relative_to(root).as_posix()
    relative = relative_text.encode("utf-8")
    size = int(item.stat().st_size)

    digest.update(len(relative).to_bytes(8, "big"))
    digest.update(relative)
    digest.update(size.to_bytes(16, "big", signed=False))

    if metadata_only:
        digest.update(b"\x00METADATA_ONLY\x00")
        return

    if not content_sha256:
        raise ValueError(f"missing content digest for directory file: {item}")
    digest.update(b"\x00CONTENT_SHA256\x00")
    digest.update(content_sha256.encode("ascii"))


# 阅读注释（函数）：处理 fingerprint 路径 相关逻辑。
def fingerprint_path(
    path: Path,
    *,
    metadata_only_paths: set[str] | None = None,
) -> tuple[str, list[str]]:
    """Fingerprint a file or directory and report metadata-only fallbacks.

    Regular files use full content SHA-256. Directory artifacts use relative
    path, size, and file content. If Windows denies reading an internal Milvus
    Lite file, that file falls back to a deterministic path-and-size marker.
    The fallback path list is stored in the index manifest so later
    verification applies the exact same strategy even if the lock state has
    changed.
    """
    target = Path(path)
    if target.is_file():
        return sha256_file(target), []
    if not target.is_dir():
        raise FileNotFoundError(f"artifact path not found: {target}")

    forced_metadata_only = set(metadata_only_paths or set())
    used_metadata_only: list[str] = []
    digest = hashlib.sha256()
    files = sorted(
        (item for item in target.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(target).as_posix(),
    )
    for item in files:
        relative_text = item.relative_to(target).as_posix()
        metadata_only = relative_text in forced_metadata_only
        if metadata_only:
            _update_directory_file_fingerprint(
                digest=digest,
                root=target,
                item=item,
                metadata_only=True,
            )
            used_metadata_only.append(relative_text)
            continue

        try:
            content_sha256 = sha256_file(item)
        except PermissionError:
            _update_directory_file_fingerprint(
                digest=digest,
                root=target,
                item=item,
                metadata_only=True,
            )
            used_metadata_only.append(relative_text)
        else:
            _update_directory_file_fingerprint(
                digest=digest,
                root=target,
                item=item,
                metadata_only=False,
                content_sha256=content_sha256,
            )

    return digest.hexdigest(), used_metadata_only


# 阅读注释（函数）：处理 sha256 路径 相关逻辑。
def sha256_path(
    path: Path,
    *,
    metadata_only_paths: set[str] | None = None,
) -> str:
    """处理 sha256 路径 相关逻辑。

    参数:
        path: 目标文件或目录路径。
        metadata_only_paths: 元数据 only paths，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：fingerprint_path。
    """
    digest, _ = fingerprint_path(path, metadata_only_paths=metadata_only_paths)
    return digest


# 阅读注释（函数）：处理 milvus semantic fingerprint 相关逻辑。
def milvus_semantic_fingerprint(
    *,
    collection_name: str,
    record_count: int,
    embedding_dim: int | None,
    metric_type: str | None,
    child_chunks_sha256: str,
    vector_records_sha256: str | None = None,
) -> str:
    """Build a stable logical fingerprint for a Milvus Lite artifact.

    Milvus Lite's on-disk directory is operational state rather than an
    immutable file artifact. Opening a collection can rewrite internal
    manifests, so byte-for-byte directory hashes are not stable across
    otherwise read-only lifecycle checks. The logical fingerprint binds the
    online store to immutable source artifacts and expected collection
    metadata; the actual store is then verified semantically by collection
    existence, row count, and self-retrieval checks.
    """
    payload = {
        "schema_version": "milvus_semantic_fingerprint_v1",
        "collection_name": str(collection_name),
        "record_count": int(record_count),
        "embedding_dim": int(embedding_dim) if embedding_dim is not None else None,
        "metric_type": str(metric_type or ""),
        "child_chunks_sha256": str(child_chunks_sha256),
        "vector_records_sha256": str(vector_records_sha256 or ""),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


# 阅读注释（函数）：处理 effective artifact integrity mode 相关逻辑。
def effective_artifact_integrity_mode(
    artifact_name: str,
    artifact: "ArtifactRecord",
) -> str:
    """Resolve integrity semantics, including legacy Milvus manifests.

    Step 11.2 manifests created before semantic integrity support stored a
    volatile directory fingerprint for ``milvus_lite``. Treat those legacy
    directory artifacts as semantic stores during verification so existing
    validated indexes remain activatable without mutating their manifests.
    """
    if artifact.integrity_mode == "milvus_semantic_v1":
        return "milvus_semantic_v1"
    if artifact_name == "milvus_lite" and artifact.kind == "directory":
        return "milvus_semantic_v1"
    return "content_sha256"


# 阅读注释（类）：封装 artifact 记录，集中封装相关状态、依赖和行为。
class ArtifactRecord(BaseModel):
    """封装 artifact 记录，集中封装相关状态、依赖和行为。"""
    model_config = ConfigDict(extra="forbid")
    path: str
    sha256: str
    record_count: int | None = None
    kind: Literal["file", "directory"] = "file"
    integrity_mode: Literal["content_sha256", "milvus_semantic_v1"] = "content_sha256"
    metadata_only_paths: list[str] = Field(default_factory=list)


# 阅读注释（类）：封装 索引 manifest，集中封装相关状态、依赖和行为。
class IndexManifest(BaseModel):
    """封装 索引 manifest，集中封装相关状态、依赖和行为。"""
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["rag_index_manifest_v1"] = "rag_index_manifest_v1"
    index_version: str
    build_id: str
    dataset_version: str
    config_hash: str
    source_hash: str
    source: dict[str, Any] | None = None
    chunker: dict[str, Any]
    embedding: dict[str, Any]
    index: dict[str, Any]
    document_count: int
    parent_chunk_count: int
    child_chunk_count: int
    artifacts: dict[str, ArtifactRecord]
    created_at: str
    reproducibility_hash: str
    notes: str | None = None

    # 阅读注释（函数）：写入 IndexManifest。
    def write(self, path: Path) -> None:
        """写入 IndexManifest。

        参数:
            path: 目标文件或目录路径。

        返回:
            None

        阅读提示:
            主要直接调用：atomic_write_text, json.dumps, self.model_dump。
        """
        atomic_write_text(
            path,
            json.dumps(
                self.model_dump(mode="json", exclude_none=True),
                ensure_ascii=False,
                indent=2,
            ),
        )


# 阅读注释（类）：封装 active 索引 pointer，集中封装相关状态、依赖和行为。
class ActiveIndexPointer(BaseModel):
    """封装 active 索引 pointer，集中封装相关状态、依赖和行为。"""
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["active_rag_index_pointer_v1"] = "active_rag_index_pointer_v1"
    index_version: str
    manifest_path: str
    manifest_sha256: str

    # 阅读注释（函数）：写入 ActiveIndexPointer。
    def write(self, path: Path) -> None:
        """写入 ActiveIndexPointer。

        参数:
            path: 目标文件或目录路径。

        返回:
            None

        阅读提示:
            主要直接调用：atomic_write_text, json.dumps, self.model_dump。
        """
        atomic_write_text(
            path,
            json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )
