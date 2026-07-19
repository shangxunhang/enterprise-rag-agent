# =============================================================================
# 中文阅读说明：命令行脚本模块，用于启动、验收、调试或离线维护。
# 主要定义：main。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from rag.registry.default_registrations import build_default_component_registry  # noqa: E402


# 阅读注释（函数）：处理 main 相关逻辑。
def main() -> int:
    """处理 main 相关逻辑。

    返回:
        int

    阅读提示:
        主要直接调用：build_default_component_registry, registry.list_components, print, json.dumps, len。
    """
    registry = build_default_component_registry()
    components = [
        {"category": item.category, "name": item.name, "version": item.version}
        for item in registry.list_components(category="chunker")
    ]
    print(json.dumps({"status": "success", "chunker_count": len(components), "chunkers": components}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
