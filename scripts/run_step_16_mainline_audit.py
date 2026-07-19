# =============================================================================
# 中文阅读说明：命令行脚本模块，用于启动、验收、调试或离线维护。
# 主要定义：main。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Generate a pre-LangGraph audit of the current native mainline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from system_test.mainline_audit import audit_mainline


# 阅读注释（函数）：处理 main 相关逻辑。
def main() -> int:
    """处理 main 相关逻辑。

    返回:
        int

    阅读提示:
        主要直接调用：argparse.ArgumentParser, parser.add_argument, str, parser.parse_args, resolve, expanduser, Path, report_path.parent.mkdir。
    """
    parser = argparse.ArgumentParser(description="Audit noop gates and SchemeWriter granularity.")
    parser.add_argument(
        "--report-path",
        default=str(
            PROJECT_ROOT
            / "data/processed/indexes/step_16_mainline_audit_report.json"
        ),
    )
    args = parser.parse_args()

    report_path = Path(args.report_path).expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = audit_mainline(PROJECT_ROOT)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("\n========================================")
    print("Step 16主链静态审计已生成")
    print(f"Generation Checker：{report['summary']['generation_checker']}")
    print(f"Repair Strategy：{report['summary']['repair_strategy']}")
    print(f"Evidence Assessor：{report['summary']['evidence_assessor']}")
    print(f"活跃风险：{report['summary']['active_risk_count']}")
    print(f"审计报告：{report_path}")
    print("========================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
