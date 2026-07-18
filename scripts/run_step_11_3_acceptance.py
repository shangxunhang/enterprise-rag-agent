from __future__ import annotations

import argparse
import json
import sys

import numpy as np
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for path in (str(BACKEND_ROOT), str(PROJECT_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from rag.offline.lifecycle import IndexLifecycleManager  # noqa: E402
from rag.offline.manifest import IndexManifest  # noqa: E402
from rag.retriever.milvus_child_retriever import MilvusChildRetriever  # noqa: E402
from rag.runtime.parent_child_runtime_factory import ParentChildRuntimeFactory  # noqa: E402
from rag.tools.rag_tool import RAGToolConfig  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Step 11.3 index lifecycle acceptance")
    parser.add_argument("--index-version", required=True)
    parser.add_argument(
        "--report-path",
        default="data/processed/indexes/step_11_3_acceptance_report.json",
    )
    parser.add_argument("--actor", default="step_11_3_acceptance")
    return parser


def main() -> int:
    args = _parser().parse_args()
    manager = IndexLifecycleManager(project_root=PROJECT_ROOT)
    checks: list[dict[str, object]] = []

    def check(name: str, condition: bool, details: dict[str, object]) -> None:
        checks.append(
            {
                "name": name,
                "status": "passed" if condition else "failed",
                "details": details,
            }
        )
        if not condition:
            raise RuntimeError(f"acceptance check failed: {name}: {details}")

    before = manager.status()
    indexes = manager.discover(persist=True)
    versions = {entry.index_version for entry in indexes}
    check(
        "index_registered",
        args.index_version in versions,
        {"registered_versions": sorted(versions)},
    )

    activation = manager.activate(
        args.index_version,
        actor=args.actor,
        reason="Step 11.3 activation acceptance",
        verify_artifact_hashes=True,
        verify_milvus=True,
        self_retrieval_samples=3,
    )
    check(
        "activation_success",
        activation.active_index_version == args.index_version,
        activation.model_dump(mode="json"),
    )

    resolved = manager.resolve_active(verify_artifacts=True)
    check(
        "online_resolver_reads_active_index",
        resolved["index_version"] == args.index_version,
        {
            "index_version": resolved["index_version"],
            "backend": resolved["backend"],
            "db_file": resolved["db_file"],
        },
    )

    manifest_file = Path(resolved["manifest_path"])
    manifest = IndexManifest.model_validate_json(
        manifest_file.read_text(encoding="utf-8")
    )
    vectors_path = Path(manifest.artifacts["vectors"].path)
    if not vectors_path.is_absolute():
        vectors_path = (manifest_file.parent / vectors_path).resolve()
    child_path = Path(resolved["child_file"])
    first_child = json.loads(
        next(line for line in child_path.read_text(encoding="utf-8").splitlines() if line.strip())
    )
    expected_child_id = str(
        first_child.get("child_chunk_id") or first_child.get("chunk_id") or ""
    )
    vectors = np.load(vectors_path, allow_pickle=False)
    retriever = MilvusChildRetriever(
        db_file=resolved["db_file"],
        collection_name=str(resolved["collection_name"]),
        metric_type=str(resolved["metric_type"] or "COSINE"),
        embedding_model=str(resolved["embedding_model"] or ""),
        hash_embedding=bool(resolved["hash_embedding"]),
        hash_dim=int(resolved["embedding_dim"] or 768),
    )
    try:
        online_hits = retriever.search_by_vector(vectors[0], top_k=3)
    finally:
        retriever.close()
    returned_ids = [str(item.get("child_chunk_id") or "") for item in online_hits]
    check(
        "online_retriever_reads_active_index",
        expected_child_id in returned_ids,
        {
            "expected_child_chunk_id": expected_child_id,
            "returned_child_chunk_ids": returned_ids,
        },
    )

    runtime_cfg = ParentChildRuntimeFactory().resolve_config(
        RAGToolConfig(),
        PROJECT_ROOT,
    )
    check(
        "online_runtime_config_uses_active_index",
        runtime_cfg.index_version == args.index_version
        and runtime_cfg.index_lineage.get("status") == "active_manifest",
        {
            "index_version": runtime_cfg.index_version,
            "lineage_status": runtime_cfg.index_lineage.get("status"),
            "db_file": runtime_cfg.db_file,
        },
    )

    pointer_dir = Path(activation.pointer_path).parent
    temp_files = [item.name for item in pointer_dir.glob(".active_index.json.*.tmp")]
    check(
        "atomic_pointer_has_no_temp_residue",
        not temp_files,
        {"temporary_files": temp_files},
    )

    rollback_result: dict[str, object]
    if before.active_index_version and before.active_index_version != args.index_version:
        rolled_back = manager.rollback(
            before.active_index_version,
            actor=args.actor,
            reason="Step 11.3 real rollback acceptance",
            verify_artifact_hashes=True,
            verify_milvus=True,
            self_retrieval_samples=1,
        )
        check(
            "rollback_to_previous_real_index",
            rolled_back.active_index_version == before.active_index_version,
            rolled_back.model_dump(mode="json"),
        )
        reactivated = manager.activate(
            args.index_version,
            actor=args.actor,
            reason="Restore accepted Step 11.3 target after rollback test",
            verify_artifact_hashes=True,
            verify_milvus=True,
            self_retrieval_samples=1,
        )
        rollback_result = {
            "status": "executed",
            "rolled_back_to": rolled_back.active_index_version,
            "restored_to": reactivated.active_index_version,
        }
    else:
        rollback_result = {
            "status": "not_executed",
            "reason": "no distinct previously active real index; rollback path is covered by unit tests",
        }
        check(
            "rollback_contract_available",
            hasattr(manager, "rollback"),
            rollback_result,
        )

    history = manager.history()
    check(
        "activation_audit_recorded",
        any(
            event.event_id == activation.event_id
            and event.to_index_version == args.index_version
            for event in history
        ),
        {"history_event_count": len(history)},
    )

    final_status = manager.status()
    check(
        "target_remains_active",
        final_status.active_index_version == args.index_version,
        final_status.model_dump(mode="json"),
    )

    failed = [item["name"] for item in checks if item["status"] != "passed"]
    report = {
        "schema_version": "step_11_3_acceptance_report_v1",
        "status": "success" if not failed else "failed",
        "stage": "step_11_3_index_lifecycle_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target_index_version": args.index_version,
        "activation": activation.model_dump(mode="json"),
        "resolved_online_index": resolved,
        "rollback": rollback_result,
        "final_status": final_status.model_dump(mode="json"),
        "checks": checks,
        "failed_checks": failed,
    }
    report_path = Path(args.report_path)
    if not report_path.is_absolute():
        report_path = (PROJECT_ROOT / report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nAcceptance report: {report_path}")
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
