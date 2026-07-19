# =============================================================================
# 中文阅读说明：命令行脚本模块，用于启动、验收、调试或离线维护。
# 主要定义：_parser、_hash_payload、main。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for path in (str(BACKEND_ROOT), str(PROJECT_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from apps.enterprise_document.services.scheme_writer.evidence_service import (  # noqa: E402
    SchemeEvidenceService,
)
from rag.offline.resolver import ActiveIndexResolver  # noqa: E402
from rag.services.rag_service import RAGService  # noqa: E402
from schemas.rag import RAGEvidenceContractSchema, RAGToolInputSchema  # noqa: E402
from schemas.tool import ToolResultSchema  # noqa: E402


# 阅读注释（函数）：处理 parser 相关逻辑。
def _parser() -> argparse.ArgumentParser:
    """处理 parser 相关逻辑。

    返回:
        argparse.ArgumentParser

    阅读提示:
        主要直接调用：argparse.ArgumentParser, parser.add_argument。
    """
    parser = argparse.ArgumentParser(
        description="Step 12 Evidence / RAG Context Contract v1 acceptance"
    )
    parser.add_argument(
        "--query",
        default="安全设计中采用了哪些认证、输入校验和敏感数据保护措施？",
    )
    parser.add_argument(
        "--report-path",
        default="data/processed/indexes/step_12_acceptance_report.json",
    )
    parser.add_argument(
        "--static-retrieval-spec",
        default=None,
        help="Optional static retrieval specification path.",
    )
    parser.add_argument("--max-context-items", type=int, default=3)
    parser.add_argument("--max-context-chars", type=int, default=6000)
    return parser


# 阅读注释（函数）：处理 hash 载荷 相关逻辑。
def _hash_payload(payload: Any) -> str:
    """处理 hash 载荷 相关逻辑。

    参数:
        payload: 跨层传递的数据载荷。

    返回:
        str

    阅读提示:
        主要直接调用：encode, json.dumps, hexdigest, hashlib.sha256。
    """
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# 阅读注释（函数）：处理 main 相关逻辑。
def main() -> int:
    """处理 main 相关逻辑。

    返回:
        int

    阅读提示:
        主要直接调用：parse_args, _parser, resolve, ActiveIndexResolver, check, bool, resolved.get, LegacyRAGService。
    """
    args = _parser().parse_args()
    checks: list[dict[str, Any]] = []

    # 阅读注释（函数）：检查 main。
    def check(name: str, condition: bool, details: dict[str, Any]) -> None:
        """检查 main。

        参数:
            name: 名称，具体约束请结合类型标注和调用方确认。
            condition: condition，具体约束请结合类型标注和调用方确认。
            details: details，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：checks.append, RuntimeError。
        """
        checks.append(
            {
                "name": name,
                "status": "passed" if condition else "failed",
                "details": details,
            }
        )
        if not condition:
            raise RuntimeError(f"acceptance check failed: {name}: {details}")

    pointer_path = PROJECT_ROOT / "data/processed/indexes/active_index.json"
    resolved = ActiveIndexResolver(
        verify_manifest_hash=True,
        verify_artifacts=True,
    ).resolve(pointer_path)
    check(
        "active_index_resolved",
        bool(resolved.get("index_version")),
        {
            "index_version": resolved.get("index_version"),
            "dataset_version": resolved.get("dataset_version"),
            "db_file": resolved.get("db_file"),
        },
    )

    service = RAGService(
        rag_project_root=PROJECT_ROOT,
        static_retrieval_spec_file=args.static_retrieval_spec,
    )
    output = service.retrieve(
        RAGToolInputSchema(
            task_id="step_12_acceptance_task",
            run_id="step_12_acceptance_run",
            agent_name="Step12Acceptance",
            query=args.query,
            max_context_items=args.max_context_items,
            max_context_chars=args.max_context_chars,
            need_citation=True,
            extra={
                "acceptance_stage": "step_12_evidence_context_contract_v1",
            },
        )
    )
    check(
        "real_rag_success",
        str(output.status.value if hasattr(output.status, "value") else output.status)
        in {"success", "partial_success"},
        {
            "status": str(output.status),
            "error": output.error.model_dump(mode="json") if output.error else None,
        },
    )
    check(
        "evidence_contract_present",
        output is not None,
        {"schema_version": output.schema_version},
    )
    contract = RAGEvidenceContractSchema.model_validate(output)
    selected_items = [
        item
        for item in contract.items
        if item.evidence_id in set(contract.selected_evidence_ids)
    ]
    dropped_items = [
        item
        for item in contract.items
        if item.evidence_id in set(contract.dropped_evidence_ids)
    ]
    check(
        "selected_evidence_non_empty",
        bool(selected_items),
        {
            "selected_count": len(selected_items),
            "dropped_count": len(dropped_items),
        },
    )
    check(
        "citation_ids_are_short_and_stable",
        bool(contract.citations)
        and [item.citation_id for item in contract.citations]
        == [f"C{index}" for index in range(1, len(contract.citations) + 1)],
        {"citation_ids": [item.citation_id for item in contract.citations]},
    )
    selected_citation_ids = [
        citation_id
        for item in selected_items
        for citation_id in item.citation_ids
    ]
    check(
        "context_is_selected_evidence_projection",
        contract.context.context_item_count == len(selected_items)
        and all(
            f"[{citation_id}]" in contract.context.context_text
            for citation_id in selected_citation_ids
        )
        and all(not item.citation_ids for item in dropped_items),
        {
            "context_chars": contract.context.used_context_chars,
            "selected_count": len(selected_items),
            "dropped_count": len(dropped_items),
            "selected_citation_ids": selected_citation_ids,
        },
    )
    check(
        "active_index_lineage_propagated",
        contract.lineage.index_version == resolved.get("index_version")
        and contract.lineage.dataset_version == resolved.get("dataset_version"),
        {
            "contract_index_version": contract.lineage.index_version,
            "active_index_version": resolved.get("index_version"),
            "contract_dataset_version": contract.lineage.dataset_version,
            "active_dataset_version": resolved.get("dataset_version"),
        },
    )
    check(
        "presence_not_misreported_as_sufficiency",
        str(
            contract.assessment.status.value
            if hasattr(contract.assessment.status, "value")
            else contract.assessment.status
        )
        == "not_assessed",
        contract.assessment.model_dump(mode="json"),
    )

    result = ToolResultSchema(
        tool_call_id="step_12_acceptance_tool_call",
        task_id=output.task_id,
        run_id=output.run_id,
        tool_name="RealRAGTool",
        success=True,
        result=output.model_dump(),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    shared_state = SimpleNamespace(task_id=output.task_id, run_id=output.run_id)
    business_context, business_chunks, business_citations, normalized = (
        SchemeEvidenceService._extract_rag_output(shared_state, result)
    )
    check(
        "scheme_consumes_contract_without_reinterpretation",
        _hash_payload(business_context.model_dump(mode="json"))
        == _hash_payload(contract.context.model_dump(mode="json"))
        and _hash_payload([item.model_dump(mode="json") for item in business_citations])
        == _hash_payload([item.model_dump(mode="json") for item in contract.citations])
        and normalized.get("evidence", {}).get("schema_version")
        == "rag_evidence_contract_v1",
        {
            "contract_hash": _hash_payload(contract.model_dump(mode="json")),
            "business_context_hash": _hash_payload(
                business_context.model_dump(mode="json")
            ),
            "selected_chunk_count": len(business_chunks),
            "citation_count": len(business_citations),
        },
    )

    failed = [item["name"] for item in checks if item["status"] != "passed"]
    report = {
        "schema_version": "step_12_acceptance_report_v1",
        "status": "success" if not failed else "failed",
        "stage": "step_12_evidence_context_contract_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "query": args.query,
        "active_index": resolved,
        "contract_summary": {
            "schema_version": contract.schema_version,
            "contract_sha256": _hash_payload(contract.model_dump(mode="json")),
            "selected_evidence_count": len(contract.selected_evidence_ids),
            "dropped_evidence_count": len(contract.dropped_evidence_ids),
            "citation_count": len(contract.citations),
            "context_chars": contract.context.used_context_chars,
            "assessment_status": str(
                contract.assessment.status.value
                if hasattr(contract.assessment.status, "value")
                else contract.assessment.status
            ),
            "index_version": contract.lineage.index_version,
            "dataset_version": contract.lineage.dataset_version,
            "embedding_model": contract.lineage.embedding_model,
            "retrieval_plan_id": contract.lineage.retrieval_plan_id,
        },
        "checks": checks,
        "failed_checks": failed,
    }
    report_path = Path(args.report_path)
    if not report_path.is_absolute():
        report_path = (PROJECT_ROOT / report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nAcceptance report: {report_path}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
