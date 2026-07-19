# =============================================================================
# 中文阅读说明：命令行脚本模块，用于启动、验收、调试或离线维护。
# 主要定义：_now_iso、_citations、_project_input、_build_package、main。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Accept compact citation projection and request-level failure isolation."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
for path in (BACKEND_ROOT, SCRIPTS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from context_manager.manager import LLMContextManager
from context_manager.policies import SectionGenerationContextPolicy
from run_demo import persist_end_to_end_artifacts
from schemas.citation import CitationSchema
from schemas.rag import RAGContextSchema


# 阅读注释（函数）：处理 now iso 相关逻辑。
def _now_iso() -> str:
    """处理 now iso 相关逻辑。

    返回:
        str

    阅读提示:
        主要直接调用：isoformat, datetime.now。
    """
    return datetime.now(timezone.utc).isoformat()


# 阅读注释（函数）：处理 citations 相关逻辑。
def _citations(count: int = 8) -> list[CitationSchema]:
    """处理 citations 相关逻辑。

    参数:
        count: count，具体约束请结合类型标注和调用方确认。

    返回:
        list[CitationSchema]

    阅读提示:
        主要直接调用：CitationSchema, range。
    """
    return [
        CitationSchema(
            citation_id=f"C{index}",
            source_type="document",
            doc_id=f"doc_{index}",
            chunk_id=f"chunk_{index}",
            title="政务云建设规范与安全技术要求" * 3,
            section=f"第{index}章 安全与架构",
            quote_text=(
                "政务云应覆盖身份认证、访问控制、日志审计、数据加密、"
                "平台架构和资源规划等建设要求。"
            )
            * 12,
        )
        for index in range(1, count + 1)
    ]


# 阅读注释（函数）：处理 项目 输入 相关逻辑。
def _project_input() -> ProjectInputSchema:
    """处理 项目 输入 相关逻辑。

    返回:
        ProjectInputSchema

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate。
    """
    return ProjectInputSchema.model_validate(
        {
            "task_id": "task_context_budget_acceptance",
            "project_name": "某政务云",
            "project_type": "政务云",
            "task_type": "scheme_generation",
            "user_query": "生成一个政务云方案",
            "generation_requirements": {
                "required_sections": ["项目概述", "建设内容", "技术方案", "安全设计"],
                "citation_required_sections": ["建设内容", "技术方案", "安全设计"],
                "max_context_chars": 6000,
                "max_tokens_per_section": 1024,
            },
            "output_schema": {
                "document_title": "某政务云建设方案",
                "required_sections": ["项目概述", "建设内容", "技术方案", "安全设计"],
            },
        }
    )


# 阅读注释（函数）：构建 package。
def _build_package(section_title: str) -> tuple[Any, Any]:
    """构建 package。

    参数:
        section_title: 章节 title，具体约束请结合类型标注和调用方确认。

    返回:
        tuple[Any, Any]

    阅读提示:
        主要直接调用：_citations, RAGContextSchema, len, _project_input, build_request, SectionGenerationContextPolicy, build, LLMContextManager。
    """
    citations = _citations()
    evidence_text = "政务云建设和安全设计证据正文。" * 800
    rag_context = RAGContextSchema(
        context_text=evidence_text,
        context_item_count=1,
        used_context_chars=len(evidence_text),
    )
    project_input = _project_input()
    request = SectionGenerationContextPolicy().build_request(
        task_id=project_input.task_id,
        run_id="run_context_budget_acceptance",
        section_id=f"section_{section_title}",
        section_title=section_title,
        section_order=1,
        project_input=project_input,
        section_contract=f"只编写{section_title}。",
        target_section_chars=1000,
        rag_context=rag_context,
        citations=citations,
        previous_sections=[],
    )
    return request, LLMContextManager().build(request)


# 阅读注释（函数）：处理 main 相关逻辑。
def main() -> int:
    """处理 main 相关逻辑。

    返回:
        int

    阅读提示:
        主要直接调用：argparse.ArgumentParser, parser.add_argument, str, parser.parse_args, resolve, expanduser, Path, report_path.parent.mkdir。
    """
    parser = argparse.ArgumentParser(description="Run context budget acceptance.")
    parser.add_argument(
        "--report-path",
        default=str(
            PROJECT_ROOT
            / "data"
            / "processed"
            / "indexes"
            / "context_budget_acceptance_report.json"
        ),
    )
    args = parser.parse_args()
    report_path = Path(args.report_path).expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    overview_request, overview_package = _build_package("项目概述")
    safety_request, safety_package = _build_package("安全设计")
    overview_catalog_request = next(
        item for item in overview_request.items if item.item_id == "citation_catalog"
    )
    safety_catalog_request = next(
        item for item in safety_request.items if item.item_id == "citation_catalog"
    )
    safety_catalog_package = next(
        item for item in safety_package.selected_items if item.item_id == "citation_catalog"
    )

    runtime_root = report_path.parent / "context_budget_runtime_failure"
    runtime_root.mkdir(parents=True, exist_ok=True)
    trace_path = runtime_root / "technical_failure_trace.jsonl"
    task_state_path = runtime_root / "technical_failure_task.json"
    trace_path.write_text(
        json.dumps(
            {
                "event_type": "run_finished",
                "phase": "error",
                "status": "failed",
                "error_message": "required rendered context cannot be truncated: citation_catalog",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    task_state_path.write_text("{}", encoding="utf-8")
    persisted = persist_end_to_end_artifacts(
        {
            "task_id": "task_context_budget_failure",
            "run_id": "run_context_budget_failure",
            "status": "failed",
            "scheme_draft": {"full_text": ""},
            "scheme_writer_output": {},
            "supervisor_result": {
                "status": "failed",
                "error": {
                    "error_code": "SCHEME_WRITER_FAILED",
                    "error_type": "ContextBudgetExceededError",
                    "message": "required rendered context cannot be truncated: citation_catalog",
                    "failed_node": "section_generation",
                    "retryable": False,
                },
            },
            "paths": {
                "trace": str(trace_path),
                "task_state": str(task_state_path),
            },
        },
        runtime_preflight={"mode": "fake"},
        expected_model_name="fake_llm",
        expected_real_rag=False,
        raise_on_validation_error=False,
    )

    checks = [
        {
            "name": "overview_catalog_is_optional",
            "passed": overview_catalog_request.required is False,
            "details": {
                "required": overview_catalog_request.required,
                "catalog_count": overview_catalog_request.metadata.get(
                    "catalog_citation_count"
                ),
            },
        },
        {
            "name": "citation_required_catalog_is_required_and_compressible",
            "passed": bool(
                safety_catalog_request.required
                and safety_catalog_request.truncate_allowed
            ),
            "details": {
                "required": safety_catalog_request.required,
                "truncate_allowed": safety_catalog_request.truncate_allowed,
                "catalog_count": safety_catalog_request.metadata.get(
                    "catalog_citation_count"
                ),
            },
        },
        {
            "name": "overview_package_fits_budget",
            "passed": bool(
                overview_package.budget.used_context_chars
                <= overview_package.budget.max_context_chars
            ),
            "details": overview_package.budget.model_dump(),
        },
        {
            "name": "safety_package_fits_budget_and_preserves_citation",
            "passed": bool(
                safety_package.budget.used_context_chars
                <= safety_package.budget.max_context_chars
                and "[C1]" in safety_catalog_package.content
            ),
            "details": {
                **safety_package.budget.model_dump(),
                "retained_catalog_chars": len(safety_catalog_package.content),
                "retained_markers": safety_catalog_package.content.count("[C"),
            },
        },
        {
            "name": "request_failure_isolated_and_reported",
            "passed": bool(
                persisted.get("validation_error")
                and Path(persisted["report_path"]).is_file()
                and Path(persisted["answer_path"]).is_file()
            ),
            "details": {
                "validation_error": persisted.get("validation_error"),
                "report_path": persisted["report_path"],
                "answer_path": persisted["answer_path"],
            },
        },
    ]
    failed = [item for item in checks if not item["passed"]]
    report = {
        "schema_version": "context_budget_acceptance_report_v1",
        "status": "success" if not failed else "failed",
        "stage": "context_budget_and_request_isolation_v1",
        "created_at": _now_iso(),
        "summary": {
            "check_count": len(checks),
            "failed_check_count": len(failed),
            "overview_used_chars": overview_package.budget.used_context_chars,
            "safety_used_chars": safety_package.budget.used_context_chars,
            "safety_retained_citation_markers": safety_catalog_package.content.count(
                "[C"
            ),
        },
        "checks": checks,
        "failed_checks": failed,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nAcceptance report: {report_path}")
    print("\n" + "=" * 48)
    if failed:
        print("Context预算与请求隔离验收失败")
    else:
        print("Context预算与请求隔离正式验收通过")
    print(f"检查项：{len(checks)}")
    print(f"失败检查：{len(failed)}")
    print(f"安全设计保留Citation：{safety_catalog_package.content.count('[C')}")
    print("=" * 48)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
