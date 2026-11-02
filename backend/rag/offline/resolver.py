"""把 active-index pointer 解析为在线检索运行时可直接使用的路径和血缘。

Resolver 只读取并校验 pointer、Manifest 和可选普通产物哈希，不创建索引、不修改
Manifest，也不切换 active pointer。Milvus Lite 的深度语义验证由生命周期激活流程
负责；在线解析阶段最多检查数据库目录存在，避免每次启动都执行昂贵自检。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rag.offline.manifest import (
    ActiveIndexPointer,
    IndexManifest,
    effective_artifact_integrity_mode,
    sha256_file,
    sha256_path,
)


def _resolve_artifact_path(
    manifest_file: Path,
    raw_path: str,
) -> Path:
    """把 Manifest 中的产物路径解析为绝对路径。

    Args:
        manifest_file: 当前 Manifest 文件路径。
        raw_path: Manifest 记录的绝对或相对路径。

    Returns:
        规范化绝对路径。相对路径以 Manifest 所在目录为基准，而不是当前工作目录。
    """

    # 第一步：展开用户目录符号并判断是否已经是绝对路径。
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    # 第二步：相对产物绑定 Manifest 目录，使版本目录可整体移动。
    return (manifest_file.parent / candidate).resolve()


class ActiveIndexResolver:
    """验证 active pointer 并返回在线运行时配置投影。

    Attributes:
        verify_manifest_hash: 是否验证 pointer 中记录的 Manifest SHA-256。
        verify_artifacts: 是否检查产物存在性和普通内容哈希。

    ``verify_artifacts=False`` 适合已由生命周期激活流程验证过的常规启动；需要更强
    启动时防篡改检查时可开启，但 Milvus 仍只检查存在性。
    """

    def __init__(
        self,
        *,
        verify_manifest_hash: bool = True,
        verify_artifacts: bool = False,
    ) -> None:
        """保存解析时的验证强度。"""

        self.verify_manifest_hash = verify_manifest_hash
        self.verify_artifacts = verify_artifacts

    def resolve(self, pointer_path: str | Path) -> dict[str, Any]:
        """解析 active pointer、Manifest 和在线所需产物路径。

        Args:
            pointer_path: active-index pointer 文件路径。

        Returns:
            包含 pointer/Manifest 哈希、索引版本、数据版本、父子块路径、Milvus 路径、
            集合、Embedding 和 Chunker 血缘的普通字典。

        Raises:
            FileNotFoundError: pointer、Manifest 或启用检查后的产物不存在时抛出。
            ValueError: 哈希、版本、必需产物或普通产物内容不匹配时抛出。
            pydantic.ValidationError: pointer 或 Manifest JSON 不符合严格协议时抛出。
        """

        # 第一步：读取并严格校验 active pointer。
        pointer_file = Path(pointer_path).expanduser().resolve()
        if not pointer_file.is_file():
            raise FileNotFoundError(
                f"active index pointer not found: {pointer_file}"
            )
        pointer = ActiveIndexPointer.model_validate_json(
            pointer_file.read_text(encoding="utf-8")
        )

        # 第二步：相对 Manifest 路径以 pointer 目录为基准解析。
        manifest_file = Path(pointer.manifest_path).expanduser()
        if not manifest_file.is_absolute():
            manifest_file = (
                pointer_file.parent / manifest_file
            ).resolve()
        else:
            manifest_file = manifest_file.resolve()
        if not manifest_file.is_file():
            raise FileNotFoundError(
                f"active index manifest not found: {manifest_file}"
            )

        # 第三步：可选验证 pointer 绑定的 Manifest 完整内容哈希。
        if (
            self.verify_manifest_hash
            and sha256_file(manifest_file) != pointer.manifest_sha256
        ):
            raise ValueError(
                "active index manifest sha256 mismatch"
            )

        # 第四步：严格解析 Manifest，并要求 pointer 与 Manifest 的版本身份一致。
        manifest = IndexManifest.model_validate_json(
            manifest_file.read_text(encoding="utf-8")
        )
        if manifest.index_version != pointer.index_version:
            raise ValueError(
                "active pointer index_version does not match manifest"
            )

        # 第五步：父块和子块始终是在线检索必需产物。
        artifacts = manifest.artifacts
        required = ["parent_chunks", "child_chunks"]
        for name in required:
            if name not in artifacts:
                raise ValueError(
                    f"active index manifest missing artifact: {name}"
                )

        # 第六步：Milvus 后端还必须声明数据库产物。
        if (
            manifest.index.get("backend") == "milvus_lite"
            and "milvus_lite" not in artifacts
        ):
            raise ValueError(
                "milvus_lite active index missing milvus_lite artifact"
            )

        # 第七步：一次性解析全部产物的实际绝对路径。
        artifact_paths = {
            name: _resolve_artifact_path(
                manifest_file,
                artifact.path,
            )
            for name, artifact in artifacts.items()
        }

        # 第八步：按需验证存在性和内容哈希。Milvus 语义存储不做目录字节比较。
        if self.verify_artifacts:
            for name, artifact in artifacts.items():
                path = artifact_paths[name]
                if not path.exists():
                    raise FileNotFoundError(
                        f"active index artifact not found: "
                        f"{name}={path}"
                    )

                if (
                    effective_artifact_integrity_mode(name, artifact)
                    != "content_sha256"
                ):
                    # 生命周期激活已检查集合、行数和自检检索；在线只确认目录存在。
                    continue

                actual = sha256_path(
                    path,
                    metadata_only_paths=set(
                        artifact.metadata_only_paths
                    ),
                )
                if actual != artifact.sha256:
                    raise ValueError(
                        f"active index artifact sha256 mismatch: {name}"
                    )

        # 第九步：返回在线运行时需要的扁平配置和可重复性血缘。
        return {
            "pointer_path": str(pointer_file),
            "pointer_hash": sha256_file(pointer_file),
            "manifest_path": str(manifest_file),
            "manifest_hash": pointer.manifest_sha256,
            "index_version": manifest.index_version,
            "dataset_version": manifest.dataset_version,
            "config_hash": manifest.config_hash,
            "reproducibility_hash": manifest.reproducibility_hash,
            "parent_file": str(artifact_paths["parent_chunks"]),
            "child_file": str(artifact_paths["child_chunks"]),
            "db_file": (
                str(artifact_paths["milvus_lite"])
                if "milvus_lite" in artifact_paths
                else None
            ),
            "collection_name": manifest.index.get(
                "collection_name"
            ),
            "metric_type": manifest.index.get("metric_type"),
            "embedding_model": manifest.embedding.get("model"),
            "embedding_version": manifest.embedding.get("version"),
            "embedding_dim": manifest.embedding.get("dim"),
            "hash_embedding": (
                manifest.embedding.get("mode") == "hash"
            ),
            "backend": manifest.index.get("backend"),
            "chunker": manifest.chunker,
        }
