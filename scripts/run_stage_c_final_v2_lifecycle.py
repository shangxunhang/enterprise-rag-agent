"""Activate, rollback-check and restore the Stage C final V2 index."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for value in (str(BACKEND_ROOT), str(PROJECT_ROOT)):
    if value not in sys.path:
        sys.path.insert(0, value)

from pymilvus import MilvusClient  # noqa: E402

from rag.offline.lifecycle import (  # noqa: E402
    IndexLifecycleManager,
    IndexRegistry,
)
from rag.offline.manifest import ActiveIndexPointer, sha256_file  # noqa: E402
from rag.offline.verification import OfflineIndexVerifier  # noqa: E402


def _pointer_snapshot(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    pointer = ActiveIndexPointer.model_validate_json(raw.decode("utf-8"))
    manifest_path = Path(pointer.manifest_path).expanduser()
    if not manifest_path.is_absolute():
        manifest_path = (path.parent / manifest_path).resolve()
    return {
        "index_version": pointer.index_version,
        "manifest_path": str(manifest_path),
        "manifest_sha256_declared": pointer.manifest_sha256,
        "manifest_sha256_actual": sha256_file(manifest_path),
        "pointer_sha256": hashlib.sha256(raw).hexdigest(),
        "manifest_hash_matches": (
            pointer.manifest_sha256 == sha256_file(manifest_path)
        ),
    }


def _load_active(manager: IndexLifecycleManager) -> dict[str, Any]:
    resolved = manager.resolve_active(verify_artifacts=True)
    client = MilvusClient(str(resolved["db_file"]))
    collection_name = str(resolved["collection_name"])
    try:
        client.load_collection(collection_name)
        stats = client.get_collection_stats(collection_name)
        row_count = int((stats or {}).get("row_count", -1))
    finally:
        client.close()
    return {
        "index_version": resolved["index_version"],
        "manifest_path": resolved["manifest_path"],
        "manifest_hash": resolved["manifest_hash"],
        "parent_file": resolved["parent_file"],
        "child_file": resolved["child_file"],
        "db_file": resolved["db_file"],
        "collection_name": collection_name,
        "milvus_row_count": row_count,
        "loaded": row_count >= 0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new-manifest", required=True)
    parser.add_argument("--expected-current", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    indexes_root = Path(r"E:\rag-agent-indexes").resolve()
    active_pointer = (
        PROJECT_ROOT / "data/processed/indexes/active_index.json"
    ).resolve()
    registry_path = (
        PROJECT_ROOT / "data/processed/indexes/index_registry.json"
    ).resolve()
    history_path = (
        PROJECT_ROOT
        / "data/processed/indexes/index_lifecycle_history.jsonl"
    ).resolve()
    output_path = Path(args.output).expanduser().resolve()
    new_manifest = Path(args.new_manifest).expanduser().resolve()

    before = _pointer_snapshot(active_pointer)
    if before["index_version"] != args.expected_current:
        raise RuntimeError(
            "active index changed before lifecycle operation; "
            f"expected={args.expected_current!r} "
            f"actual={before['index_version']!r}"
        )
    if not before["manifest_hash_matches"]:
        raise RuntimeError("pre-activation pointer manifest hash mismatch")

    registry = IndexRegistry(
        indexes_root,
        registry_path=registry_path,
        history_path=history_path,
    )
    manager = IndexLifecycleManager(
        root_dir=indexes_root,
        active_pointer_path=active_pointer,
        registry=registry,
    )

    new_verification = OfflineIndexVerifier().verify(
        new_manifest,
        verify_artifact_hashes=True,
        verify_milvus=True,
        self_retrieval_samples=10,
    )
    if new_verification.status != "success":
        raise RuntimeError(
            "new index verification failed: "
            f"{new_verification.metrics.get('failed_checks')}"
        )

    registered = manager.register(new_manifest)
    activated = manager.activate(
        new_manifest,
        verification_result=new_verification,
        actor="stage_c_final_v2",
        reason="Stage C final V2 acceptance activation",
    )
    after_activate = _pointer_snapshot(active_pointer)
    loaded_new_first = _load_active(manager)

    rollback = manager.rollback(
        target_index_version=args.expected_current,
        actor="stage_c_final_v2",
        reason="Verify previous active remains loadable rollback target",
        verify_artifact_hashes=True,
        verify_milvus=True,
        self_retrieval_samples=3,
    )
    after_rollback = _pointer_snapshot(active_pointer)
    loaded_rollback = _load_active(manager)

    restored = manager.activate(
        new_manifest,
        verification_result=new_verification,
        actor="stage_c_final_v2",
        reason="Restore Stage C final V2 after rollback verification",
    )
    final_pointer = _pointer_snapshot(active_pointer)
    loaded_new_final = _load_active(manager)

    registry_entries = {
        entry.index_version: entry.to_dict()
        for entry in registry.list_entries()
        if entry.index_version
        in {args.expected_current, new_verification.index_version}
    }
    report = {
        "schema_version": "stage_c_final_v2_lifecycle_report_v1",
        "passed": all(
            (
                after_activate["manifest_hash_matches"],
                after_rollback["manifest_hash_matches"],
                final_pointer["manifest_hash_matches"],
                loaded_new_first["loaded"],
                loaded_rollback["loaded"],
                loaded_new_final["loaded"],
                after_activate["index_version"]
                == new_verification.index_version,
                after_rollback["index_version"] == args.expected_current,
                final_pointer["index_version"]
                == new_verification.index_version,
            )
        ),
        "before": before,
        "registered": registered.to_dict(),
        "activate_new": activated.to_dict(),
        "after_activate": after_activate,
        "loaded_new_first": loaded_new_first,
        "rollback": rollback.to_dict(),
        "after_rollback": after_rollback,
        "loaded_rollback": loaded_rollback,
        "restore_new": restored.to_dict(),
        "final_pointer": final_pointer,
        "loaded_new_final": loaded_new_final,
        "registry_entries": registry_entries,
        "registry_path": str(registry_path),
        "history_path": str(history_path),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "before": before["index_version"],
                "activated": after_activate["index_version"],
                "rolled_back": after_rollback["index_version"],
                "final": final_pointer["index_version"],
                "final_pointer_sha256": final_pointer["pointer_sha256"],
                "final_manifest_sha256": final_pointer[
                    "manifest_sha256_actual"
                ],
                "new_milvus_rows": loaded_new_final["milvus_row_count"],
                "rollback_milvus_rows": loaded_rollback[
                    "milvus_row_count"
                ],
                "output": str(output_path),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
