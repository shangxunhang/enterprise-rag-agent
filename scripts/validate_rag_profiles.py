"""Validate every online RAG profile without loading heavy ML resources."""

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

from rag.config.profile_catalog import OnlineRAGProfileCatalogValidator  # noqa: E402
from rag.registry.default_registrations import (  # noqa: E402
    build_default_component_registry,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
        help="Agent-RAG project root.",
    )
    parser.add_argument(
        "--profile-dir",
        default="backend/rag/profiles",
        help="Profile directory, absolute or relative to project root.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the complete validation report as JSON.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = OnlineRAGProfileCatalogValidator().validate(
        project_root=Path(args.project_root),
        profile_dir=args.profile_dir,
        registry=build_default_component_registry(),
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return
    print("RAG profile catalog validation: success")
    print(f"Profile directory: {report.profile_dir}")
    print(f"Profile count: {report.profile_count}")
    for item in report.profiles:
        print(
            f"- {item.profile_id}@{item.profile_version} "
            f"schema={item.schema_version} "
            f"components={item.component_count} "
            f"hash={item.config_hash}"
        )


if __name__ == "__main__":
    main()
