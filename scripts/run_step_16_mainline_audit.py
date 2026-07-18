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


def main() -> int:
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
    print(f"Evidence Grader：{report['summary']['evidence_grader']}")
    print(f"活跃风险：{report['summary']['active_risk_count']}")
    print(f"审计报告：{report_path}")
    print("========================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
