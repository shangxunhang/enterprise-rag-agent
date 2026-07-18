from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from rag.registry.default_registrations import build_default_component_registry  # noqa: E402


def main() -> int:
    registry = build_default_component_registry()
    components = [
        {"category": item.category, "name": item.name, "version": item.version}
        for item in registry.list_components(category="chunker")
    ]
    print(json.dumps({"status": "success", "chunker_count": len(components), "chunkers": components}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
