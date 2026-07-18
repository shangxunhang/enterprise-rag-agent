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
from rag.offline.config import OfflineIndexConfigLoader  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a versioned offline RAG index")
    parser.add_argument("--index-config", required=True)
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    loader = OfflineIndexConfigLoader()
    config_path = loader.resolve_path(args.index_config, project_root=PROJECT_ROOT)
    config = loader.load(config_path, project_root=PROJECT_ROOT)
    builder = OfflineIndexBuilder(project_root=PROJECT_ROOT)
    result = builder.validate(config) if args.validate_only else builder.build(config)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
