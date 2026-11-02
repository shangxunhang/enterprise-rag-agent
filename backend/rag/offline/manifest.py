"""定义不可变索引 Manifest、产物指纹和 active-index pointer。

普通文件按完整字节内容做 SHA-256；目录按相对路径、文件大小和内容哈希形成稳定
指纹。Milvus Lite 是会在打开时改写内部文件的运行数据库，因此新 Manifest 使用
逻辑语义指纹，生命周期验证再检查集合存在性、行数和自检检索，而不是要求目录字节
永久不变。Manifest 和 pointer 都采用同目录临时文件加 ``os.replace`` 原子发布。
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def atomic_write_text(path: Path, text: str) -> None:
    """以同目录临时文件原子替换 UTF-8 文本文件。

    Args:
        path: 最终目标文件。
        text: 要完整发布的文本内容。

    写入流程会 flush 并 fsync 临时文件，再通过 ``os.replace`` 原子切换名称。读取方
    只会看到旧完整版本或新完整版本，不会看到半写入文件。
    """

    # 第一步：创建目标父目录，并生成同目录随机临时文件。
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.{uuid.uuid4().hex}.tmp"
    )

    try:
        # 第二步：写入、刷新 Python 缓冲并要求操作系统落盘临时文件。
        with temporary.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())

        # 第三步：原子替换正式目标。
        os.replace(temporary, target)
    finally:
        # 第四步：写入或替换失败时清理残留临时文件。
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def sha256_file(path: Path) -> str:
    """以 1 MiB 分块计算普通文件的内容 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(block)
    return digest.hexdigest()


def _update_directory_file_fingerprint(
    *,
    digest: "hashlib._Hash",
    root: Path,
    item: Path,
    metadata_only: bool,
    content_sha256: str | None = None,
) -> None:
    """把一个目录内文件的身份、大小和可选内容哈希加入总指纹。

    Args:
        digest: 目录级增量 SHA-256 对象。
        root: 被指纹化目录根路径。
        item: 当前普通文件。
        metadata_only: 是否只记录相对路径和文件大小。
        content_sha256: 非 metadata-only 模式下必需的文件内容哈希。

    相对路径长度、路径字节和文件大小使用定长前缀编码，避免简单字符串拼接产生边界
    歧义。metadata-only 模式会写入显式标记，因此不会与完整内容模式碰撞。
    """

    # 第一步：按 POSIX 风格记录相对路径，使 Windows/Unix 分隔符不影响指纹协议。
    relative_text = item.relative_to(root).as_posix()
    relative = relative_text.encode("utf-8")
    size = int(item.stat().st_size)

    # 第二步：写入路径长度、路径和非负文件大小。
    digest.update(len(relative).to_bytes(8, "big"))
    digest.update(relative)
    digest.update(size.to_bytes(16, "big", signed=False))

    # 第三步：受锁文件只记录 metadata-only 标记。
    if metadata_only:
        digest.update(b"\x00METADATA_ONLY\x00")
        return

    # 第四步：完整模式必须提供内容哈希，并写入独立协议标记。
    if not content_sha256:
        raise ValueError(
            f"missing content digest for directory file: {item}"
        )
    digest.update(b"\x00CONTENT_SHA256\x00")
    digest.update(content_sha256.encode("ascii"))


def fingerprint_path(
    path: Path,
    *,
    metadata_only_paths: set[str] | None = None,
) -> tuple[str, list[str]]:
    """为普通文件或目录生成指纹，并报告 metadata-only 回退路径。

    Args:
        path: 待指纹化文件或目录。
        metadata_only_paths: 必须按相对路径只使用路径和大小的文件集合，通常来自旧
            Manifest 已记录的 Windows 锁文件列表。

    Returns:
        ``(digest, used_metadata_only_paths)``。普通文件第二项为空列表。

    Raises:
        FileNotFoundError: 目标既不是文件也不是目录时抛出。

    目录内文件按相对路径排序。无法读取内容的文件只在 ``PermissionError`` 时回退
    metadata-only，并把实际回退列表写入 Manifest，保证以后即使锁状态改变也沿用
    相同策略。
    """

    target = Path(path)

    # 普通文件直接使用完整内容哈希。
    if target.is_file():
        return sha256_file(target), []

    if not target.is_dir():
        raise FileNotFoundError(
            f"artifact path not found: {target}"
        )

    # 第一步：准备强制回退集合、实际回退列表和目录级摘要。
    forced_metadata_only = set(metadata_only_paths or set())
    used_metadata_only: list[str] = []
    digest = hashlib.sha256()

    # 第二步：递归枚举普通文件并按相对 POSIX 路径排序，保证跨运行顺序稳定。
    files = sorted(
        (
            item
            for item in target.rglob("*")
            if item.is_file()
        ),
        key=lambda item: item.relative_to(target).as_posix(),
    )

    # 第三步：逐文件追加完整内容或 metadata-only 指纹。
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
            # Windows 上 Milvus Lite 内部文件可能被锁定，只记录路径和大小。
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


def sha256_path(
    path: Path,
    *,
    metadata_only_paths: set[str] | None = None,
) -> str:
    """返回文件或目录指纹，不暴露 metadata-only 回退列表。"""

    digest, _ = fingerprint_path(
        path,
        metadata_only_paths=metadata_only_paths,
    )
    return digest


def milvus_semantic_fingerprint(
    *,
    collection_name: str,
    record_count: int,
    embedding_dim: int | None,
    metric_type: str | None,
    child_chunks_sha256: str,
    vector_records_sha256: str | None = None,
) -> str:
    """构造 Milvus Lite 在线存储的稳定逻辑指纹。

    Args:
        collection_name: 目标集合名称。
        record_count: 预期实体数量。
        embedding_dim: 向量维度。
        metric_type: 检索度量。
        child_chunks_sha256: 不可变子块文件哈希。
        vector_records_sha256: 可选向量索引记录文件哈希。

    Milvus Lite 打开集合时可能重写内部 Manifest，因此目录字节哈希不适合作为长期
    完整性身份。该指纹绑定集合协议和不可变来源文件；实际数据库再由验证器检查集合、
    行数和向量自检索。
    """

    payload = {
        "schema_version": "milvus_semantic_fingerprint_v1",
        "collection_name": str(collection_name),
        "record_count": int(record_count),
        "embedding_dim": (
            int(embedding_dim)
            if embedding_dim is not None
            else None
        ),
        "metric_type": str(metric_type or ""),
        "child_chunks_sha256": str(child_chunks_sha256),
        "vector_records_sha256": str(
            vector_records_sha256 or ""
        ),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def effective_artifact_integrity_mode(
    artifact_name: str,
    artifact: "ArtifactRecord",
) -> str:
    """解析产物实际应使用的完整性语义。

    新 Manifest 显式标记 ``milvus_semantic_v1``。旧 Step 11.2 Manifest 曾把
    ``milvus_lite`` 目录记录为普通目录哈希；为保持已验证索引可激活，读取时将该特定
    旧组合推断为语义模式，但不修改不可变 Manifest。
    """

    if artifact.integrity_mode == "milvus_semantic_v1":
        return "milvus_semantic_v1"
    if artifact_name == "milvus_lite" and artifact.kind == "directory":
        return "milvus_semantic_v1"
    return "content_sha256"


class ArtifactRecord(BaseModel):
    """描述一个索引产物的位置、完整性身份和记录数量。

    Attributes:
        path: 相对 Manifest 或绝对文件系统路径。
        sha256: 内容哈希或 Milvus 语义指纹。
        record_count: 可选逻辑记录数。
        kind: 普通文件或目录。
        integrity_mode: 内容哈希或 Milvus 语义模式。
        metadata_only_paths: 目录指纹中只按路径和大小处理的相对文件列表。
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    path: str
    sha256: str
    record_count: int | None = None
    kind: Literal["file", "directory"] = "file"
    integrity_mode: Literal[
        "content_sha256",
        "milvus_semantic_v1",
    ] = "content_sha256"
    metadata_only_paths: list[str] = Field(default_factory=list)


class IndexManifest(BaseModel):
    """固化一个版本化离线索引的完整血缘和产物清单。

    Attributes:
        schema_version: Manifest 协议版本。
        index_version: 由索引兼容性身份生成的稳定版本。
        build_id: 本次构建操作身份。
        dataset_version: 源数据逻辑版本。
        config_hash: 完整构建配置哈希。
        source_hash: 源数据实际内容哈希。
        source: 可选源数据配置快照。
        chunker: Chunker 名称、版本和参数快照。
        embedding: Embedding 模式、模型、版本和维度。
        index: 索引后端、集合和度量参数。
        document_count: 输入文档数。
        parent_chunk_count: 生成父块数。
        child_chunk_count: 生成子块和向量行数。
        artifacts: 名称到产物记录的映射。
        created_at: 配置指定的确定性构建时间。
        reproducibility_hash: 数据、配置和产物综合可重复性哈希。
        notes: 可选人工备注。
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["rag_index_manifest_v1"] = (
        "rag_index_manifest_v1"
    )
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

    def write(self, path: Path) -> None:
        """以原子替换方式写入格式化 JSON Manifest。"""

        atomic_write_text(
            path,
            json.dumps(
                self.model_dump(
                    mode="json",
                    exclude_none=True,
                ),
                ensure_ascii=False,
                indent=2,
            ),
        )


class ActiveIndexPointer(BaseModel):
    """指向当前在线索引 Manifest 的最小原子控制文件。

    Attributes:
        schema_version: pointer 协议版本。
        index_version: 期望激活的索引版本。
        manifest_path: Manifest 路径，可相对 pointer 文件目录。
        manifest_sha256: Manifest 文件完整内容哈希。

    pointer 不复制产物路径和组件配置，在线运行时必须继续解析并校验 Manifest。
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["active_rag_index_pointer_v1"] = (
        "active_rag_index_pointer_v1"
    )
    index_version: str
    manifest_path: str
    manifest_sha256: str

    def write(self, path: Path) -> None:
        """以原子替换方式写入 active-index pointer。"""

        atomic_write_text(
            path,
            json.dumps(
                self.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
        )
