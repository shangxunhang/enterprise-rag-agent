"""Read-only Stage D.1 audit against the immutable active Stage C index."""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from pydantic import ValidationError  # noqa: E402

from rag.application.retrieval_scope import (  # noqa: E402
    resolve_canonical_retrieval_scope,
)
from rag.catalog.document_profile import (  # noqa: E402
    CURRENT_PROJECT_CLAIM_TYPES,
    TEMPLATE_ONLY_CLAIM_TYPES,
    ClaimType,
    EvidenceRole,
    ProjectRelation,
)
from rag.offline.resolver import ActiveIndexResolver  # noqa: E402
from rag.retriever.bm25_child_retriever import (  # noqa: E402
    load_jsonl_dicts,
    normalize_child_chunk_record,
)
from rag.retriever.milvus_child_retriever import (  # noqa: E402
    normalize_milvus_child_hit,
)
from rag.schema.retrieval_candidate_v2 import (  # noqa: E402
    build_retrieval_candidate_v2,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pointer",
        default="data/processed/indexes/active_index.json",
    )
    parser.add_argument(
        "--output",
        default="data/processed/indexes/stage_d_final_active_audit_v3.json",
    )
    parser.add_argument("--no-artifact-hash-check", action="store_true")
    return parser


def _resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _stable(values: Iterable[Any]) -> list[str]:
    return list(
        dict.fromkeys(str(item) for item in values if str(item or "").strip())
    )


def _candidate_record(raw: dict[str, Any], rank: int) -> dict[str, Any]:
    record = dict(raw)
    record.setdefault("rank", rank)
    record.setdefault("child_text", str(raw.get("text") or ""))
    record.setdefault("parent_text", str(raw.get("text") or ""))
    record.setdefault("score", 1.0 / max(rank, 1))
    return record


def _claim_values(candidate: Any) -> tuple[set[ClaimType], set[ClaimType]]:
    return set(candidate.allowed_claim_types), set(candidate.forbidden_claim_types)


def _dense_hit_from_child(raw: dict[str, Any]) -> dict[str, Any]:
    entity = {
        key: raw.get(key)
        for key in (
            "chunk_id",
            "child_chunk_id",
            "parent_chunk_id",
            "tenant_id",
            "kb_id",
            "file_id",
            "doc_id",
            "document_type",
            "evidence_role",
            "project_relation",
            "project_id",
            "authority_level",
            "text",
            "page_start",
            "page_end",
            "title",
            "section",
        )
    }
    entity["extra_json"] = json.dumps(raw.get("extra") or {}, ensure_ascii=False)
    return {"distance": 0.9, "entity": entity}


def _projection_mismatches(raw: dict[str, Any], candidate: Any) -> list[str]:
    extra = raw.get("extra")
    extra = extra if isinstance(extra, dict) else {}
    structured = candidate.structured_evidence
    mismatches: list[str] = []
    if structured.fact is not None:
        fact = structured.fact
        expected = {
            "record_id": extra.get("record_id"),
            "fact_category": extra.get("fact_category"),
            "subject": extra.get("subject"),
            "predicate": extra.get("predicate"),
            "value": extra.get("value"),
            "unit": extra.get("unit"),
            "fact_status": extra.get("fact_status") or "unknown",
            "source_span": extra.get("source_span"),
        }
        actual = fact.model_dump(mode="json")
        for field_name, expected_value in expected.items():
            if actual.get(field_name) != expected_value:
                mismatches.append(f"fact.{field_name}")
    if structured.table is not None:
        table = structured.table.model_dump(mode="json")
        for field_name in (
            "table_id",
            "table_title",
            "header",
            "row_index",
            "cells",
            "normalized_fields",
            "inherited_cells",
            "merged_cell_relations",
            "calculation_record",
        ):
            raw_value = extra.get(field_name)
            actual_value = table.get(field_name)
            if raw_value is None and actual_value in (None, [], {}):
                continue
            if actual_value != raw_value:
                mismatches.append(f"table.{field_name}")
    if structured.policy is not None:
        policy = structured.policy.model_dump(mode="json")
        for field_name in (
            "policy_name",
            "chapter_id",
            "chapter_title",
            "article_id",
            "article_title",
            "clause_id",
            "clause_path",
            "is_table_of_contents",
        ):
            raw_value = extra.get(field_name)
            actual_value = policy.get(field_name)
            if raw_value is None and actual_value in (None, [], False):
                continue
            if actual_value != raw_value:
                mismatches.append(f"policy.{field_name}")
    if structured.template is not None:
        template = structured.template.model_dump(mode="json")
        for field_name in (
            "section_path",
            "section_title",
            "required_fields",
            "placeholder_fields",
            "style_hints",
            "is_example_content",
        ):
            raw_value = extra.get(field_name)
            actual_value = template.get(field_name)
            if raw_value is None and actual_value in (None, [], False):
                continue
            if actual_value != raw_value:
                mismatches.append(f"template.{field_name}")
    return mismatches


def _sample_payload(candidate: Any) -> dict[str, Any]:
    structured = candidate.structured_evidence.model_dump(
        mode="json", exclude_none=True
    )
    return {
        "candidate_id": candidate.candidate_id,
        "document_id": candidate.document_id,
        "kb_id": candidate.kb_id,
        "project_relation": candidate.project_relation.value,
        "authority_level": candidate.authority_level.value,
        "structured_evidence": structured,
        "text_preview": candidate.match_text[:500],
    }


def audit(pointer_path: Path, *, verify_artifacts: bool) -> dict[str, Any]:
    resolved = ActiveIndexResolver(
        verify_manifest_hash=True,
        verify_artifacts=verify_artifacts,
    ).resolve(pointer_path)
    manifest_path = Path(resolved["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_child_count = int(
        manifest.get("artifacts", {})
        .get("child_chunks", {})
        .get("record_count", 0)
        or 0
    )
    child_records = load_jsonl_dicts(resolved["child_file"])

    errors: list[dict[str, Any]] = []
    structured_projection_errors: list[dict[str, Any]] = []
    dense_bm25_mismatches: list[str] = []
    violations: dict[str, list[str]] = {
        "duplicate_candidate_ids": [],
        "default_kb_ids": [],
        "unknown_or_unclassified": [],
        "non_indexable_or_gold": [],
        "historical_current_claim_leak": [],
        "template_non_template_claim_leak": [],
        "current_claim_wrong_project_relation": [],
        "profile_scalar_mismatch": [],
    }
    candidate_ids: list[str] = []
    profile_hashes: set[str] = set()
    source_hashes: set[str] = set()
    document_ids: set[str] = set()
    distribution = {
        "kb_id": Counter(),
        "document_type": Counter(),
        "evidence_role": Counter(),
        "project_relation": Counter(),
        "authority_level": Counter(),
        "chunk_strategy": Counter(),
        "representation_type": Counter(),
        "fact_status": Counter(),
    }
    structured_counts = Counter()
    samples: dict[str, dict[str, Any]] = {}
    successfully_built: list[tuple[dict[str, Any], Any]] = []

    for rank, raw in enumerate(child_records, start=1):
        raw_id = str(
            raw.get("child_chunk_id") or raw.get("chunk_id") or f"row_{rank}"
        )
        try:
            candidate = build_retrieval_candidate_v2(
                _candidate_record(raw, rank)
            )
        except Exception as exc:
            errors.append(
                {
                    "child_chunk_id": raw_id,
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                }
            )
            continue

        successfully_built.append((raw, candidate))
        candidate_ids.append(candidate.candidate_id)
        profile_hashes.add(candidate.profile_sha256)
        source_hashes.add(candidate.source_sha256)
        document_ids.add(candidate.document_id)
        profile = candidate.document_profile
        allowed, forbidden = _claim_values(candidate)
        structured = candidate.structured_evidence

        distribution["kb_id"][candidate.kb_id] += 1
        distribution["document_type"][candidate.document_type.value] += 1
        distribution["evidence_role"][candidate.evidence_role.value] += 1
        distribution["project_relation"][candidate.project_relation.value] += 1
        distribution["authority_level"][candidate.authority_level.value] += 1
        distribution["chunk_strategy"][str(
            candidate.type_aware_chunking.get("actual_strategy")
            or candidate.type_aware_chunking.get("strategy")
            or "unknown"
        )] += 1
        distribution["representation_type"][structured.representation_type] += 1
        structured_counts["structured_evidence_records"] += 1
        if structured.fact is not None:
            structured_counts["fact_evidence_records"] += 1
            distribution["fact_status"][structured.fact.fact_status.value] += 1
        if structured.table is not None:
            structured_counts["table_evidence_records"] += 1
        if structured.policy is not None:
            structured_counts["policy_evidence_records"] += 1
        if structured.template is not None:
            structured_counts["template_evidence_records"] += 1
        if structured.fallback.used:
            structured_counts["fallback_evidence_records"] += 1
        if not any(
            (
                structured.fact is not None,
                structured.table is not None,
                structured.policy is not None,
                structured.template is not None,
            )
        ):
            structured_counts["plain_text_evidence_records"] += 1

        mismatches = _projection_mismatches(raw, candidate)
        if mismatches:
            structured_projection_errors.append(
                {"candidate_id": candidate.candidate_id, "fields": mismatches}
            )

        if "current_project_fact" not in samples and (
            structured.fact is not None
            and candidate.project_relation is ProjectRelation.CURRENT_PROJECT
        ):
            samples["current_project_fact"] = _sample_payload(candidate)
        if "estimated_cost" not in samples and (
            structured.fact is not None
            and structured.fact.fact_status.value == "estimated"
            and str(structured.fact.fact_category or "") == "cost"
        ):
            samples["estimated_cost"] = _sample_payload(candidate)
        if "unknown_fact" not in samples and (
            structured.fact is not None
            and structured.fact.fact_status.value == "unknown"
            and structured.fact.predicate
        ):
            samples["unknown_fact"] = _sample_payload(candidate)
        if "policy_clause" not in samples and structured.policy is not None:
            samples["policy_clause"] = _sample_payload(candidate)
        if "table_row" not in samples and structured.table is not None:
            samples["table_row"] = _sample_payload(candidate)
        if "template_section" not in samples and structured.template is not None:
            samples["template_section"] = _sample_payload(candidate)
        if "fallback_text" not in samples and structured.fallback.used:
            samples["fallback_text"] = _sample_payload(candidate)

        if candidate.kb_id == "default":
            violations["default_kb_ids"].append(candidate.candidate_id)
        if (
            candidate.document_type.value == "unknown"
            or candidate.evidence_role is EvidenceRole.UNCLASSIFIED
            or candidate.project_relation is ProjectRelation.UNKNOWN
        ):
            violations["unknown_or_unclassified"].append(candidate.candidate_id)
        if bool(profile.get("is_gold_reference")) or not bool(
            profile.get("should_index", True)
        ):
            violations["non_indexable_or_gold"].append(candidate.candidate_id)
        if (
            candidate.evidence_role is EvidenceRole.HISTORICAL_CASE
            and allowed & CURRENT_PROJECT_CLAIM_TYPES
        ):
            violations["historical_current_claim_leak"].append(
                candidate.candidate_id
            )
        if (
            candidate.evidence_role is EvidenceRole.TEMPLATE_STYLE
            and allowed - TEMPLATE_ONLY_CLAIM_TYPES
        ):
            violations["template_non_template_claim_leak"].append(
                candidate.candidate_id
            )
        if (
            allowed & CURRENT_PROJECT_CLAIM_TYPES
            and candidate.project_relation is not ProjectRelation.CURRENT_PROJECT
        ):
            violations["current_claim_wrong_project_relation"].append(
                candidate.candidate_id
            )
        if allowed & forbidden:
            errors.append(
                {
                    "child_chunk_id": candidate.candidate_id,
                    "error_type": "ClaimBoundaryOverlap",
                    "message": "allowed and forbidden claim types overlap",
                }
            )

    violations["duplicate_candidate_ids"] = [
        item for item, count in Counter(candidate_ids).items() if count > 1
    ]

    # Compare both online physical branches on representative real records.
    seen_representations: set[str] = set()
    for raw, candidate in successfully_built:
        representation = candidate.structured_evidence.representation_type
        if representation in seen_representations:
            continue
        seen_representations.add(representation)
        dense = normalize_milvus_child_hit(_dense_hit_from_child(raw), rank=1)
        keyword = normalize_child_chunk_record(raw)
        if dense.get("structured_evidence") != keyword.get("structured_evidence"):
            dense_bm25_mismatches.append(candidate.candidate_id)

    scope_cases: list[dict[str, Any]] = []
    scope_negative_cases: list[dict[str, Any]] = []
    tenant_ids = _stable(
        candidate.tenant_id for _, candidate in successfully_built
    )
    kb_ids = sorted(distribution["kb_id"].keys())
    if tenant_ids:
        tenant_id = tenant_ids[0]
        for kb_id in kb_ids:
            filter_expr = (
                f'tenant_id == {json.dumps(tenant_id, ensure_ascii=False)} and '
                f'kb_id in [{json.dumps(kb_id, ensure_ascii=False)}]'
            )
            scope = resolve_canonical_retrieval_scope(
                filter_expr=filter_expr,
                keyword_scope=None,
                access_scope_enforced=True,
            )
            scope_cases.append(
                {
                    "tenant_id": tenant_id,
                    "kb_id": kb_id,
                    "passed": (
                        scope.tenant_id == tenant_id
                        and list(scope.kb_ids) == [kb_id]
                    ),
                }
            )

        ordered = resolve_canonical_retrieval_scope(
            filter_expr=(
                f'tenant_id == {json.dumps(tenant_id)} and '
                f'kb_id in [{json.dumps(kb_ids[-1])}, {json.dumps(kb_ids[0])}]'
            ),
            keyword_scope={
                "tenant_id": tenant_id,
                "kb_ids": [kb_ids[0], kb_ids[-1]],
                "access_scope_enforced": True,
            },
            access_scope_enforced=True,
        )
        scope_negative_cases.append(
            {
                "case": "set_order_independent",
                "passed": list(ordered.kb_ids) == sorted({kb_ids[0], kb_ids[-1]}),
            }
        )
        try:
            resolve_canonical_retrieval_scope(
                filter_expr=(
                    f'tenant_id == {json.dumps(tenant_id)} and '
                    f'kb_id in [{json.dumps(kb_ids[0])}]'
                ),
                keyword_scope={
                    "tenant_id": tenant_id,
                    "kb_ids": ["__mismatched_kb__"],
                },
                access_scope_enforced=True,
            )
        except ValueError as exc:
            scope_negative_cases.append(
                {
                    "case": "dense_keyword_mismatch",
                    "passed": True,
                    "message": str(exc),
                }
            )
        else:
            scope_negative_cases.append(
                {"case": "dense_keyword_mismatch", "passed": False}
            )
        try:
            resolve_canonical_retrieval_scope(
                filter_expr=(
                    f'tenant_id == {json.dumps(tenant_id)} and '
                    f'kb_id in [{json.dumps(kb_ids[0])}]'
                ),
                keyword_scope={
                    "tenant_id": tenant_id,
                    "kb_ids": [kb_ids[0]],
                    "kb_idz": ["template_style_kb"],
                },
                access_scope_enforced=True,
            )
        except (ValidationError, ValueError) as exc:
            scope_negative_cases.append(
                {
                    "case": "unknown_scope_field",
                    "passed": True,
                    "message": str(exc),
                }
            )
        else:
            scope_negative_cases.append(
                {"case": "unknown_scope_field", "passed": False}
            )

    candidate_mismatch_rejected = False
    if successfully_built:
        mutated = _candidate_record(deepcopy(successfully_built[0][0]), 1)
        mutated["kb_id"] = "__profile_mismatch__"
        try:
            build_retrieval_candidate_v2(mutated)
        except Exception:
            candidate_mismatch_rejected = True

    required_samples = {
        "current_project_fact",
        "estimated_cost",
        "unknown_fact",
        "policy_clause",
        "table_row",
        "template_section",
        "fallback_text",
    }
    hard_failures = {
        "child_count_mismatch": expected_child_count != len(child_records),
        "candidate_build_errors": bool(errors),
        "candidate_count_mismatch": len(candidate_ids) != len(child_records),
        "structured_evidence_projection_missing": (
            structured_counts["structured_evidence_records"] != len(child_records)
            or bool(structured_projection_errors)
        ),
        "dense_bm25_structured_evidence_mismatch": bool(
            dense_bm25_mismatches
        ),
        "candidate_profile_scalar_mismatch_not_rejected": not candidate_mismatch_rejected,
        "required_structured_samples_missing": bool(required_samples - set(samples)),
        "scope_projection_failure": any(not item["passed"] for item in scope_cases),
        "scope_negative_case_failure": any(
            not item["passed"] for item in scope_negative_cases
        ),
        **{key: bool(value) for key, value in violations.items()},
    }
    passed = not any(hard_failures.values())
    return {
        "schema_version": "stage_d_final_active_audit_v3",
        "passed": passed,
        "active_index": {
            "index_version": resolved["index_version"],
            "pointer_path": resolved["pointer_path"],
            "pointer_hash": resolved["pointer_hash"],
            "manifest_path": resolved["manifest_path"],
            "manifest_hash": resolved["manifest_hash"],
            "child_file": resolved["child_file"],
            "expected_child_count": expected_child_count,
        },
        "counts": {
            "child_records": len(child_records),
            "retrieval_candidate_v2": len(candidate_ids),
            "distinct_candidate_ids": len(set(candidate_ids)),
            "distinct_documents": len(document_ids),
            "distinct_profile_hashes": len(profile_hashes),
            "distinct_source_hashes": len(source_hashes),
            "candidate_build_errors": len(errors),
            **dict(structured_counts),
        },
        "distribution": {
            key: dict(sorted(counter.items()))
            for key, counter in distribution.items()
        },
        "structured_samples": samples,
        "structured_projection_errors": structured_projection_errors,
        "dense_bm25_mismatch_candidate_ids": dense_bm25_mismatches,
        "scope_audit": {
            "projection_cases": scope_cases,
            "negative_cases": scope_negative_cases,
        },
        "violations": violations,
        "candidate_errors": errors,
        "hard_failures": hard_failures,
    }


def main() -> int:
    args = _parser().parse_args()
    output_path = _resolve_path(args.output)
    report = audit(
        _resolve_path(args.pointer),
        verify_artifacts=not args.no_artifact_hash_check,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
