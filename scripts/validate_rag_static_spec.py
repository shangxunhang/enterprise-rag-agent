"""Validate the one online static retrieval specification without ML loading."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_FILE = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_FILE.parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for item in (BACKEND_ROOT, PROJECT_ROOT):
    value = str(item)
    if value not in sys.path:
        sys.path.insert(0, value)

from rag.config.static_retrieval import StaticRetrievalSpecLoader  # noqa: E402
from rag.registry.default_registrations import (  # noqa: E402
    build_default_component_registry,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument(
        "--spec",
        default="backend/rag/config/static_retrieval_v1.yaml",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = Path(args.project_root).expanduser().resolve()
    loader = StaticRetrievalSpecLoader()
    path = loader.resolve_path(args.spec, project_root=root)
    spec = loader.load(path, project_root=root)
    registry = build_default_component_registry()
    references = [
        *(("query_transformer", item) for item in spec.query_transformers),
        *(("retriever", item) for item in spec.retrievers),
        ("source_fusion", spec.source_fusion),
        ("query_fusion", spec.query_fusion),
        ("candidate_enricher", spec.candidate_enricher),
        ("reranker", spec.reranker),
        ("evidence_assessor", spec.evidence_assessor),
        ("corrective_retrieval_gate", spec.corrective_retrieval_gate),
        ("corrective_query_planner", spec.corrective_query_planner),
        *(("context_packer", item) for item in spec.context_packers),
    ]
    missing = [
        f"{category}/{config.name}@{config.version}"
        for category, config in references
        if not registry.contains(
            category=category,
            name=config.name,
            version=config.version,
        )
    ]
    if missing:
        raise ValueError("unregistered components: " + ", ".join(missing))
    report = {
        "status": "success",
        "path": str(path),
        "schema_version": spec.schema_version,
        "spec_id": spec.spec_id,
        "spec_version": spec.spec_version,
        "component_count": len(references),
        "hash": spec.config_hash(),
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(
        f"Static retrieval spec validation: success\n"
        f"{spec.spec_id}@{spec.spec_version} components={len(references)} "
        f"hash={spec.config_hash()}"
    )


if __name__ == "__main__":
    main()
