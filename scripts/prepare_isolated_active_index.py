"""Create an isolated online-runtime copy of an existing active Milvus index.

The source pointer, manifest, and index are never modified.  The target gets a
new Milvus directory plus a rewritten manifest and active pointer.  This is a
test/runtime isolation utility for Windows MilvusLite lock contention.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from rag.offline.manifest import (
    ActiveIndexPointer,
    IndexManifest,
    sha256_file,
)


def _resolve(base_file: Path, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (base_file.parent / candidate).resolve()


def prepare(source_pointer: Path, target_root: Path) -> Path:
    source_pointer = source_pointer.expanduser().resolve()
    target_root = target_root.expanduser().resolve()
    if not source_pointer.is_file():
        raise FileNotFoundError(f"source active pointer not found: {source_pointer}")
    if target_root.exists():
        raise FileExistsError(f"target runtime root already exists: {target_root}")

    pointer = ActiveIndexPointer.model_validate_json(
        source_pointer.read_text(encoding="utf-8")
    )
    source_manifest_path = _resolve(source_pointer, pointer.manifest_path)
    if sha256_file(source_manifest_path) != pointer.manifest_sha256:
        raise ValueError("source pointer manifest SHA-256 mismatch")
    manifest = IndexManifest.model_validate_json(
        source_manifest_path.read_text(encoding="utf-8")
    )
    milvus_artifact = manifest.artifacts.get("milvus_lite")
    if milvus_artifact is None:
        raise ValueError("source manifest has no milvus_lite artifact")
    source_db = _resolve(source_manifest_path, milvus_artifact.path)
    if not source_db.is_dir():
        raise FileNotFoundError(f"source Milvus directory not found: {source_db}")

    target_db = target_root / "milvus.db"
    target_root.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source_db, target_db)

    artifacts = dict(manifest.artifacts)
    artifacts["milvus_lite"] = milvus_artifact.model_copy(
        update={"path": str(target_db)}
    )
    index_payload = dict(manifest.index)
    index_payload["output_dir"] = str(target_root)
    isolated_manifest = manifest.model_copy(
        update={"artifacts": artifacts, "index": index_payload},
        deep=True,
    )
    target_manifest = target_root / "index_manifest.json"
    isolated_manifest.write(target_manifest)

    target_pointer = target_root / "data" / "processed" / "indexes" / "active_index.json"
    target_pointer.parent.mkdir(parents=True, exist_ok=True)
    ActiveIndexPointer(
        index_version=isolated_manifest.index_version,
        manifest_path=str(target_manifest),
        manifest_sha256=sha256_file(target_manifest),
    ).write(target_pointer)
    return target_pointer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-pointer", required=True)
    parser.add_argument("--target-root", required=True)
    args = parser.parse_args()
    target_pointer = prepare(
        Path(args.source_pointer),
        Path(args.target_root),
    )
    print(f"isolated_active_pointer={target_pointer}")
    print(f"isolated_runtime_root={target_pointer.parents[3]}")


if __name__ == "__main__":
    main()
