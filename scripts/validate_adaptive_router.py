"""Validate an Adaptive Profile router and print one explainable decision."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from rag.routing.runtime import AdaptiveProfileRouterRuntime


def _csv(value: str | None) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Adaptive Profile routing.")
    parser.add_argument(
        "--router-config",
        default="backend/rag/routing/adaptive_router_v1.yaml",
    )
    parser.add_argument(
        "--query",
        default="根据资料生成企业级 RAG-Agent 系统建设方案",
    )
    parser.add_argument("--task-type", default="scheme_generation")
    parser.add_argument("--document-title", default="企业级 RAG-Agent 系统建设方案")
    parser.add_argument(
        "--required-sections",
        default="项目概述,建设目标,建设内容,技术方案,资源配置,安全设计,实施与验收,待补充事项",
    )
    parser.add_argument(
        "--citation-required-sections",
        default="建设内容,技术方案,安全设计",
    )
    parser.add_argument("--requested-profile-id", default=None)
    parser.add_argument("--no-citation", action="store_true")
    args = parser.parse_args()

    config_path = Path(args.router_config).expanduser()
    if not config_path.is_absolute():
        config_path = (PROJECT_ROOT / config_path).resolve()

    runtime = AdaptiveProfileRouterRuntime(
        config_file=config_path,
        project_root=PROJECT_ROOT,
    )
    payload = {
        "query": args.query,
        "need_citation": not args.no_citation,
        "extra_metadata": {
            "task_type": args.task_type,
            "document_context": {
                "document_title": args.document_title,
                "required_sections": _csv(args.required_sections),
                "citation_required_sections": _csv(
                    args.citation_required_sections
                ),
            },
        },
    }
    if args.requested_profile_id:
        payload["extra_metadata"]["requested_profile_id"] = (
            args.requested_profile_id
        )

    print(
        json.dumps(
            {
                "status": "success",
                "validation": runtime.validation_report(),
                "decision": runtime.route(payload).to_dict(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
