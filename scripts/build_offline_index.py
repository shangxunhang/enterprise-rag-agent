# =============================================================================
# 中文阅读说明：命令行脚本模块，用于启动、验收、调试或离线维护。
# 主要定义：parse_args、main。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
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


# 阅读注释（函数）：解析 args。
def parse_args() -> argparse.Namespace:
    """解析 args。

    返回:
        argparse.Namespace

    阅读提示:
        主要直接调用：argparse.ArgumentParser, parser.add_argument, parser.parse_args。
    """
    parser = argparse.ArgumentParser(description="Build a versioned offline RAG index")
    parser.add_argument("--index-config", required=True)
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args()


# 阅读注释（函数）：处理 main 相关逻辑。
def main() -> int:
    """处理 main 相关逻辑。

    返回:
        int

    阅读提示:
        主要直接调用：parse_args, OfflineIndexConfigLoader, loader.resolve_path, loader.load, OfflineIndexBuilder, builder.validate, builder.build, print。
    """
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
