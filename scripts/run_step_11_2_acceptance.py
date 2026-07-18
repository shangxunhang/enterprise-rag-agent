from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for path in (str(BACKEND_ROOT), str(PROJECT_ROOT / "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

from rag.offline.builder import OfflineIndexBuilder  # noqa: E402
from rag.offline.config import OfflineIndexBuildConfig, OfflineIndexConfigLoader  # noqa: E402
from rag.offline.verification import OfflineIndexVerifier  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Step 11.2 acceptance: build and verify a real m3e-base + Milvus Lite index"
    )
    parser.add_argument("--index-config", required=True, help="Offline index YAML/JSON profile")
    parser.add_argument("--source-path", help="Override source.path from the profile")
    parser.add_argument("--embedding-model", help="Override embedding.model_name")
    parser.add_argument("--device", choices=("cpu", "cuda", "mps"), help="Override embedding.device")
    parser.add_argument("--batch-size", type=int, help="Override embedding.batch_size")
    parser.add_argument("--output-root", help="Override outputs.root_dir")
    parser.add_argument("--dataset-version", help="Override dataset_version; bump when source changes")
    parser.add_argument("--skip-build", action="store_true", help="Only verify an existing manifest")
    parser.add_argument("--manifest-path", help="Manifest to verify when --skip-build is used")
    parser.add_argument("--skip-artifact-hashes", action="store_true")
    parser.add_argument("--skip-milvus", action="store_true")
    parser.add_argument("--self-retrieval-samples", type=int, default=3)
    parser.add_argument("--self-retrieval-top-k", type=int, default=3)
    parser.add_argument("--report-path", help="Acceptance report path")
    return parser.parse_args()


def apply_overrides(config: OfflineIndexBuildConfig, args: argparse.Namespace) -> OfflineIndexBuildConfig:
    payload = config.model_dump(mode="python")
    if args.source_path:
        payload["source"]["path"] = args.source_path
    if args.embedding_model:
        payload["embedding"]["model_name"] = args.embedding_model
    if args.device:
        payload["embedding"]["device"] = args.device
    if args.batch_size:
        payload["embedding"]["batch_size"] = args.batch_size
    if args.output_root:
        payload["outputs"]["root_dir"] = args.output_root
    if args.dataset_version:
        payload["dataset_version"] = args.dataset_version
    return OfflineIndexBuildConfig.model_validate(payload)


def main() -> int:
    args = parse_args()
    loader = OfflineIndexConfigLoader()
    config = loader.load(args.index_config, project_root=PROJECT_ROOT)
    config = apply_overrides(config, args)

    if config.index.backend != "milvus_lite":
        raise ValueError(
            "Step 11.2 acceptance requires index.backend=milvus_lite; "
            "artifacts_only is only a unit/smoke build"
        )
    if config.embedding.mode != "model":
        raise ValueError(
            "Step 11.2 acceptance requires embedding.mode=model; hash embedding is not accepted"
        )
    if config.outputs.update_active_pointer:
        raise ValueError(
            "Step 11.2 must not activate active_index; set outputs.update_active_pointer=false"
        )

    build_payload: dict[str, object]
    if args.skip_build:
        if not args.manifest_path:
            raise ValueError("--manifest-path is required with --skip-build")
        manifest_path = Path(args.manifest_path).expanduser()
        if not manifest_path.is_absolute():
            manifest_path = (PROJECT_ROOT / manifest_path).resolve()
        build_payload = {
            "status": "skipped",
            "manifest_path": str(manifest_path),
        }
    else:
        build_result = OfflineIndexBuilder(project_root=PROJECT_ROOT).build(config)
        if not build_result.manifest_path:
            raise RuntimeError("offline build did not produce a manifest")
        manifest_path = Path(build_result.manifest_path).resolve()
        build_payload = build_result.to_dict()

    verification = OfflineIndexVerifier().verify(
        manifest_path,
        verify_artifact_hashes=not args.skip_artifact_hashes,
        verify_milvus=not args.skip_milvus,
        self_retrieval_samples=max(1, args.self_retrieval_samples),
        self_retrieval_top_k=max(1, args.self_retrieval_top_k),
    )
    report = {
        "schema_version": "step_11_2_acceptance_report_v1",
        "status": verification.status,
        "stage": "step_11_2_real_data_m3e_milvus_lite",
        "build": build_payload,
        "verification": verification.to_dict(),
    }
    report_path = (
        Path(args.report_path).expanduser()
        if args.report_path
        else manifest_path.parent / "step_11_2_acceptance_report.json"
    )
    if not report_path.is_absolute():
        report_path = (PROJECT_ROOT / report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nAcceptance report: {report_path}")
    return 0 if verification.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
