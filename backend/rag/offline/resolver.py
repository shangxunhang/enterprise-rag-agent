"""Resolve an active index pointer into online-runtime paths and lineage."""
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


def _resolve_artifact_path(manifest_file: Path, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (manifest_file.parent / candidate).resolve()


class ActiveIndexResolver:
    def __init__(self, *, verify_manifest_hash: bool = True, verify_artifacts: bool = False) -> None:
        self.verify_manifest_hash = verify_manifest_hash
        self.verify_artifacts = verify_artifacts

    def resolve(self, pointer_path: str | Path) -> dict[str, Any]:
        pointer_file = Path(pointer_path).expanduser().resolve()
        if not pointer_file.is_file():
            raise FileNotFoundError(f"active index pointer not found: {pointer_file}")
        pointer = ActiveIndexPointer.model_validate_json(pointer_file.read_text(encoding="utf-8"))
        manifest_file = Path(pointer.manifest_path).expanduser()
        if not manifest_file.is_absolute():
            manifest_file = (pointer_file.parent / manifest_file).resolve()
        else:
            manifest_file = manifest_file.resolve()
        if not manifest_file.is_file():
            raise FileNotFoundError(f"active index manifest not found: {manifest_file}")
        if self.verify_manifest_hash and sha256_file(manifest_file) != pointer.manifest_sha256:
            raise ValueError("active index manifest sha256 mismatch")
        manifest = IndexManifest.model_validate_json(manifest_file.read_text(encoding="utf-8"))
        if manifest.index_version != pointer.index_version:
            raise ValueError("active pointer index_version does not match manifest")

        artifacts = manifest.artifacts
        required = ["parent_chunks", "child_chunks"]
        for name in required:
            if name not in artifacts:
                raise ValueError(f"active index manifest missing artifact: {name}")
        if manifest.index.get("backend") == "milvus_lite" and "milvus_lite" not in artifacts:
            raise ValueError("milvus_lite active index missing milvus_lite artifact")

        artifact_paths = {
            name: _resolve_artifact_path(manifest_file, artifact.path)
            for name, artifact in artifacts.items()
        }
        if self.verify_artifacts:
            for name, artifact in artifacts.items():
                path = artifact_paths[name]
                if not path.exists():
                    raise FileNotFoundError(f"active index artifact not found: {name}={path}")
                if effective_artifact_integrity_mode(name, artifact) != "content_sha256":
                    # Milvus Lite is an operational directory whose internal
                    # files may change when opened. Lifecycle activation runs
                    # semantic checks (collection, row count, self-retrieval);
                    # the online resolver only verifies existence here.
                    continue
                actual = sha256_path(
                    path,
                    metadata_only_paths=set(artifact.metadata_only_paths),
                )
                if actual != artifact.sha256:
                    raise ValueError(f"active index artifact sha256 mismatch: {name}")

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
            "collection_name": manifest.index.get("collection_name"),
            "metric_type": manifest.index.get("metric_type"),
            "embedding_model": manifest.embedding.get("model"),
            "embedding_version": manifest.embedding.get("version"),
            "embedding_dim": manifest.embedding.get("dim"),
            "hash_embedding": manifest.embedding.get("mode") == "hash",
            "backend": manifest.index.get("backend"),
            "chunker": manifest.chunker,
        }
