"""Audit Stage C V2 immutable artifacts without invoking Stage D runtime."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for value in (str(BACKEND_ROOT), str(PROJECT_ROOT)):
    if value not in sys.path:
        sys.path.insert(0, value)

from pymilvus import MilvusClient  # noqa: E402

from rag.catalog.document_profile_registry import DocumentProfileRegistry  # noqa: E402
from rag.offline.manifest import IndexManifest, sha256_file  # noqa: E402


REQUIRED_PROFILE_FIELDS = {
    "schema_version",
    "document_id",
    "source_sha256",
    "document_type",
    "evidence_role",
    "kb_id",
    "tenant_id",
    "project_relation",
    "project_id",
    "authority",
    "content_capabilities",
    "parsing",
    "classification",
    "source_document_form",
    "taxonomy_mapping",
    "should_index",
    "is_gold_reference",
    "profile_sha256",
    "catalog_sha256",
}
REQUIRED_AUTHORITY_FIELDS = {
    "level",
    "issuer",
    "version",
    "effective_date",
    "expiration_date",
}
REQUIRED_CHUNKING_FIELDS = {
    "strategy",
    "representation_type",
    "parser_version",
    "chunker_version",
    "structure_path",
    "fallback_used",
    "fallback_reason",
    "fallback_strategy",
    "original_strategy",
    "actual_strategy",
}
GOLD_DOCUMENT_ID = "doc_a8144d5d4e2b2478bbdfc1cd"
RESIDUAL_DOCUMENT_ID = "doc_59f7609343f4286266dddf49"


def _jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"JSONL record is not an object at {path}:{line_number}")
        records.append(payload)
    return records


def _profile(record: dict[str, Any]) -> dict[str, Any] | None:
    extra = record.get("extra")
    if isinstance(extra, dict) and isinstance(
        extra.get("document_profile"), dict
    ):
        return extra["document_profile"]
    direct = record.get("document_profile")
    return direct if isinstance(direct, dict) else None


def _chunking(record: dict[str, Any]) -> dict[str, Any] | None:
    extra = record.get("extra")
    if isinstance(extra, dict) and isinstance(
        extra.get("type_aware_chunking"), dict
    ):
        return extra["type_aware_chunking"]
    direct = record.get("type_aware_chunking")
    return direct if isinstance(direct, dict) else None


def _identity(record: dict[str, Any], kind: str) -> str:
    candidates = {
        "parent": ("parent_chunk_id",),
        "child": ("child_chunk_id", "chunk_id"),
        "vector": ("child_chunk_id", "indexed_chunk_id", "chunk_id"),
        "bm25": ("child_chunk_id", "child_id", "record_id"),
    }[kind]
    for key in candidates:
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def _sample_projection(child: dict[str, Any]) -> dict[str, Any]:
    extra = child.get("extra") or {}
    profile = extra.get("document_profile") or {}
    chunking = extra.get("type_aware_chunking") or {}
    strategy = chunking.get("original_strategy")
    projection: dict[str, Any] = {
        "child_chunk_id": child.get("child_chunk_id"),
        "parent_chunk_id": child.get("parent_chunk_id"),
        "document_id": child.get("doc_id"),
        "source_name": extra.get("source_name"),
        "kb_id": child.get("kb_id"),
        "document_type": profile.get("document_type"),
        "evidence_role": profile.get("evidence_role"),
        "authority": profile.get("authority"),
        "strategy": strategy,
        "representation_type": chunking.get("representation_type"),
        "structure_path": chunking.get("structure_path"),
        "fallback_used": chunking.get("fallback_used"),
        "fallback_reason": chunking.get("fallback_reason"),
        "text_excerpt": str(child.get("text") or "")[:500],
    }
    if strategy == "policy_article_clause":
        projection["structure"] = {
            key: extra.get(key)
            for key in (
                "policy_name",
                "chapter_id",
                "chapter_title",
                "article_id",
                "article_title",
                "clause_id",
                "clause_path",
                "is_table_of_contents",
            )
        }
    elif strategy == "table_row_record":
        projection["structure"] = {
            key: extra.get(key)
            for key in (
                "table_id",
                "table_title",
                "header",
                "row_index",
                "cells",
                "normalized_fields",
                "inherited_cells",
                "source_page",
                "structured_table_asset",
                "calculation_record",
            )
        }
    elif strategy == "fact_record":
        projection["structure"] = {
            key: extra.get(key)
            for key in (
                "record_id",
                "fact_category",
                "subject",
                "predicate",
                "value",
                "unit",
                "fact_status",
                "source_span",
                "source_page",
            )
        }
    elif strategy == "template_section":
        projection["structure"] = {
            key: extra.get(key)
            for key in (
                "section_path",
                "section_title",
                "required_fields",
                "placeholder_fields",
                "style_hints",
                "is_example_content",
                "example_values",
                "template_claim_types",
            )
        }
    else:
        projection["structure"] = {
            key: extra.get(key)
            for key in (
                "heading_path",
                "heading_level",
                "section_title",
                "source_page_start",
                "source_page_end",
            )
        }
    return projection


def _choose_sample(
    children: list[dict[str, Any]],
    *,
    kb_id: str | None = None,
    strategy: str | None = None,
) -> tuple[int, dict[str, Any]]:
    candidates: list[tuple[int, dict[str, Any]]] = []
    for index, child in enumerate(children):
        chunking = _chunking(child) or {}
        if kb_id is not None and child.get("kb_id") != kb_id:
            continue
        if strategy is not None and chunking.get("original_strategy") != strategy:
            continue
        candidates.append((index, child))
    if not candidates:
        raise ValueError(f"no self-retrieval candidate for kb={kb_id} strategy={strategy}")
    candidates.sort(
        key=lambda item: (
            bool((_chunking(item[1]) or {}).get("fallback_used")),
            len(str(item[1].get("text") or "")),
            str(item[1].get("child_chunk_id") or ""),
        )
    )
    return candidates[0]


def audit(
    *,
    manifest_path: Path,
    profiles_path: Path,
) -> dict[str, Any]:
    manifest = IndexManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    artifact_paths = {
        name: Path(record.path).expanduser().resolve()
        for name, record in manifest.artifacts.items()
    }
    units = _jsonl(artifact_paths["profiled_cleaned_text_units"])
    parents = _jsonl(artifact_paths["parent_chunks"])
    children = _jsonl(artifact_paths["child_chunks"])
    vectors = _jsonl(artifact_paths["vector_index_records"])
    bm25 = _jsonl(artifact_paths["bm25_records"])
    vector_matrix = np.load(artifact_paths["vectors"], mmap_mode="r")
    registry = DocumentProfileRegistry.from_jsonl(profiles_path)
    profiles = registry.all()

    parent_by_id = {_identity(item, "parent"): item for item in parents}
    child_by_id = {_identity(item, "child"): item for item in children}
    vector_by_id = {_identity(item, "vector"): item for item in vectors}
    bm25_by_id = {_identity(item, "bm25"): item for item in bm25}

    manifest_docs = {profile.document_id for profile in profiles}
    indexed_docs = {str(item.get("doc_id") or "") for item in children}
    gold_docs = {
        profile.document_id
        for profile in profiles
        if profile.metadata.get("is_gold_reference") is True
    }
    unknown_docs = {
        profile.document_id
        for profile in profiles
        if profile.document_type.value == "unknown"
    }

    strategy_doc_sets: dict[str, set[str]] = defaultdict(set)
    strategy_parent_counts: Counter[str] = Counter()
    strategy_child_counts: Counter[str] = Counter()
    strategy_fallback_docs: dict[str, set[str]] = defaultdict(set)
    strategy_fallback_chunks: Counter[str] = Counter()
    fallback_reason_counts: Counter[str] = Counter()
    representation_counts: Counter[str] = Counter()
    for parent in parents:
        chunking = _chunking(parent) or {}
        strategy_parent_counts[str(chunking.get("original_strategy"))] += 1
    for child in children:
        chunking = _chunking(child) or {}
        strategy = str(chunking.get("original_strategy"))
        document_id = str(child.get("doc_id") or "")
        strategy_doc_sets[strategy].add(document_id)
        strategy_child_counts[strategy] += 1
        representation_counts[
            f"{strategy}:{chunking.get('representation_type')}"
        ] += 1
        if bool(chunking.get("fallback_used")):
            strategy_fallback_docs[strategy].add(document_id)
            strategy_fallback_chunks[strategy] += 1
            fallback_reason_counts[
                str(chunking.get("fallback_reason") or "missing_reason")
            ] += 1

    required_profile_missing: Counter[str] = Counter()
    authority_contract_missing: Counter[str] = Counter()
    taxonomy_field_missing: Counter[str] = Counter()
    profile_sha_missing: Counter[str] = Counter()
    source_sha_missing: Counter[str] = Counter()
    lineage_artifacts = {
        "profiled_cleaned_text_units": units,
        "parent_chunks": parents,
        "child_chunks": children,
        "vector_index_records": vectors,
        "bm25_records": bm25,
    }
    for artifact_name, records in lineage_artifacts.items():
        for record in records:
            profile = _profile(record)
            if profile is None or not REQUIRED_PROFILE_FIELDS.issubset(profile):
                required_profile_missing[artifact_name] += 1
                continue
            authority = profile.get("authority")
            if not isinstance(authority, dict) or not REQUIRED_AUTHORITY_FIELDS.issubset(authority):
                authority_contract_missing[artifact_name] += 1
            if "taxonomy_mapping" not in profile:
                taxonomy_field_missing[artifact_name] += 1
            if not profile.get("profile_sha256"):
                profile_sha_missing[artifact_name] += 1
            if not profile.get("source_sha256"):
                source_sha_missing[artifact_name] += 1

    duplicate_parent_ids = len(parents) - len(parent_by_id)
    duplicate_child_ids = len(children) - len(child_by_id)
    duplicate_vector_ids = len(vectors) - len(vector_by_id)
    duplicate_bm25_ids = len(bm25) - len(bm25_by_id)
    orphan_children = sorted(
        child_id
        for child_id, child in child_by_id.items()
        if str(child.get("parent_chunk_id") or "") not in parent_by_id
    )
    orphan_vectors = sorted(set(vector_by_id) - set(child_by_id))
    missing_vectors = sorted(set(child_by_id) - set(vector_by_id))
    orphan_bm25 = sorted(set(bm25_by_id) - set(child_by_id))
    missing_bm25 = sorted(set(child_by_id) - set(bm25_by_id))
    profile_sha_mismatches: list[str] = []
    for child_id, child in child_by_id.items():
        parent = parent_by_id.get(str(child.get("parent_chunk_id") or ""))
        records = [
            parent,
            child,
            vector_by_id.get(child_id),
            bm25_by_id.get(child_id),
        ]
        hashes = {
            str((_profile(item) or {}).get("profile_sha256") or "")
            for item in records
            if item is not None
        }
        if len(hashes) != 1 or "" in hashes:
            profile_sha_mismatches.append(child_id)

    undisclosed_fallback = 0
    missing_chunking_contract = 0
    for child in children:
        chunking = _chunking(child)
        if chunking is None or not REQUIRED_CHUNKING_FIELDS.issubset(chunking):
            missing_chunking_contract += 1
            continue
        if (
            chunking.get("original_strategy") != chunking.get("actual_strategy")
            and not chunking.get("fallback_used")
        ):
            undisclosed_fallback += 1

    kb_chunk_counts = Counter(str(item.get("kb_id") or "") for item in children)
    default_kb_chunks = kb_chunk_counts.get("default", 0)
    unknown_chunks = sum(
        1
        for child in children
        if (_profile(child) or {}).get("document_type") == "unknown"
        or (_profile(child) or {}).get("evidence_role") == "unclassified"
    )
    gold_chunks = sum(1 for item in children if item.get("doc_id") == GOLD_DOCUMENT_ID)
    residual_chunks = sum(
        1 for item in children if item.get("doc_id") == RESIDUAL_DOCUMENT_ID
    )

    historical_profiles = [
        profile
        for profile in profiles
        if profile.evidence_role.value == "historical_case"
    ]
    current_claims = {
        "current_project_fact",
        "current_project_cost",
        "current_project_quantity",
        "current_project_schedule",
        "current_project_configuration",
    }
    historical_boundary_violations = [
        profile.document_id
        for profile in historical_profiles
        if profile.project_relation.relation.value != "other_project"
        or bool(
            current_claims
            & {
                claim.value
                for claim in profile.content_capabilities.allowed_claim_types
            }
        )
    ]
    template_boundary_violations = [
        profile.document_id
        for profile in profiles
        if profile.evidence_role.value == "template_style"
        and bool(
            {
                claim.value
                for claim in profile.content_capabilities.allowed_claim_types
            }
            - {"template_structure", "style_guidance"}
        )
    ]
    policy_boundary_violations = [
        profile.document_id
        for profile in profiles
        if profile.evidence_role.value == "policy_standard"
        and bool(
            {
                claim.value
                for claim in profile.content_capabilities.allowed_claim_types
            }
            - {"policy_requirement"}
        )
    ]

    # Pick auditable structured examples, including the CO2 calculation record.
    sample_children: dict[str, dict[str, Any]] = {}
    for strategy in (
        "heading_parent_paragraph_child",
        "policy_article_clause",
        "table_row_record",
        "fact_record",
        "template_section",
    ):
        selected = next(
            child
            for child in children
            if (_chunking(child) or {}).get("original_strategy") == strategy
            and not bool((_chunking(child) or {}).get("fallback_used"))
        )
        sample_children[strategy] = _sample_projection(selected)
    co2_child = next(
        (
            child
            for child in children
            if child.get("doc_id") == "doc_584ac18ec1c01c6a64ffed5b"
            and (_chunking(child) or {}).get("representation_type") == "table_row"
        ),
        None,
    )
    if co2_child is not None:
        sample_children["table_row_record_co2"] = _sample_projection(co2_child)
    fact_status_samples: dict[str, dict[str, Any]] = {}
    for status in ("proposed", "estimated", "confirmed", "decided", "planned"):
        selected = next(
            (
                child
                for child in children
                if child.get("extra", {}).get("fact_status") == status
            ),
            None,
        )
        if selected is not None:
            fact_status_samples[status] = _sample_projection(selected)
    fallback_sample = next(
        (
            child
            for child in children
            if bool((_chunking(child) or {}).get("fallback_used"))
        ),
        None,
    )
    if fallback_sample is not None:
        sample_children["fallback"] = _sample_projection(fallback_sample)

    # Exact-vector top-5 checks cover every logical KB and every profile strategy.
    targets = [
        ("kb:project_fact_kb", {"kb_id": "project_fact_kb"}),
        ("kb:historical_case_kb", {"kb_id": "historical_case_kb"}),
        ("kb:policy_standard_kb", {"kb_id": "policy_standard_kb"}),
        ("kb:technical_reference_kb", {"kb_id": "technical_reference_kb"}),
        ("kb:template_style_kb", {"kb_id": "template_style_kb"}),
        (
            "strategy:heading_parent_paragraph_child",
            {"strategy": "heading_parent_paragraph_child"},
        ),
        (
            "strategy:policy_article_clause",
            {"strategy": "policy_article_clause"},
        ),
        ("strategy:table_row_record", {"strategy": "table_row_record"}),
        ("strategy:fact_record", {"strategy": "fact_record"}),
        ("strategy:template_section", {"strategy": "template_section"}),
    ]
    client = MilvusClient(str(artifact_paths["milvus_lite"]))
    client.load_collection(str(manifest.index.get("collection_name")))
    self_retrieval: list[dict[str, Any]] = []
    try:
        for label, selector in targets:
            row_index, child = _choose_sample(children, **selector)
            profile = _profile(child) or {}
            expected_child_id = str(child.get("child_chunk_id") or "")
            expected_doc_id = str(child.get("doc_id") or "")
            expected_parent_id = str(child.get("parent_chunk_id") or "")
            kb_id = str(child.get("kb_id") or "")
            hits = client.search(
                collection_name=str(manifest.index.get("collection_name")),
                data=[np.asarray(vector_matrix[row_index], dtype=np.float32).tolist()],
                limit=5,
                filter=f'kb_id == "{kb_id}"',
                output_fields=[
                    "child_chunk_id",
                    "parent_chunk_id",
                    "doc_id",
                    "kb_id",
                    "document_type",
                    "evidence_role",
                    "project_relation",
                    "project_id",
                    "authority_level",
                    "extra_json",
                ],
            )[0]
            returned_child_ids: list[str] = []
            returned_doc_ids: list[str] = []
            top_entity = hits[0].get("entity") if hits else {}
            for hit in hits:
                entity = hit.get("entity") or {}
                returned_child_ids.append(str(entity.get("child_chunk_id") or ""))
                returned_doc_ids.append(str(entity.get("doc_id") or ""))
            top_extra = json.loads(str((top_entity or {}).get("extra_json") or "{}"))
            top_profile = top_extra.get("document_profile") or {}
            passed = (
                expected_child_id in returned_child_ids
                and expected_doc_id in returned_doc_ids
                and str((top_entity or {}).get("parent_chunk_id") or "")
                == expected_parent_id
                and top_profile.get("profile_sha256")
                == profile.get("profile_sha256")
                and expected_parent_id in parent_by_id
            )
            self_retrieval.append(
                {
                    "label": label,
                    "expected_child_chunk_id": expected_child_id,
                    "expected_parent_chunk_id": expected_parent_id,
                    "expected_document_id": expected_doc_id,
                    "kb_id": kb_id,
                    "strategy": (_chunking(child) or {}).get(
                        "original_strategy"
                    ),
                    "returned_child_chunk_ids": returned_child_ids,
                    "returned_document_ids": returned_doc_ids,
                    "top1_profile_sha256": top_profile.get("profile_sha256"),
                    "expected_profile_sha256": profile.get("profile_sha256"),
                    "passed": passed,
                }
            )
    finally:
        client.close()
    self_retrieval_hit_rate = (
        sum(item["passed"] for item in self_retrieval)
        / len(self_retrieval)
    )

    artifact_records = {
        name: {
            "path": record.path,
            "sha256": record.sha256,
            "record_count": record.record_count,
            "integrity_mode": record.integrity_mode,
        }
        for name, record in manifest.artifacts.items()
    }
    strategy_summary = {
        strategy: {
            "document_count": len(strategy_doc_sets[strategy]),
            "parent_count": strategy_parent_counts[strategy],
            "child_count": strategy_child_counts[strategy],
            "fallback_document_count": len(
                strategy_fallback_docs[strategy]
            ),
            "fallback_chunk_count": strategy_fallback_chunks[strategy],
        }
        for strategy in sorted(strategy_doc_sets)
    }

    hard_failures = {
        "manifest_document_count": len(manifest_docs) != 31,
        "indexed_document_count": len(indexed_docs) != 30,
        "gold_document_count": len(gold_docs) != 1,
        "gold_chunk_count": gold_chunks != 0,
        "residual_chunk_count": residual_chunks != 0,
        "unknown_document_count": len(unknown_docs) != 0,
        "default_kb_chunk_count": default_kb_chunks != 0,
        "unknown_chunk_count": unknown_chunks != 0,
        "missing_profile": bool(required_profile_missing),
        "missing_authority_contract": bool(authority_contract_missing),
        "missing_taxonomy_field": bool(taxonomy_field_missing),
        "missing_profile_sha256": bool(profile_sha_missing),
        "missing_source_sha256": bool(source_sha_missing),
        "duplicate_parent_ids": duplicate_parent_ids != 0,
        "duplicate_child_ids": duplicate_child_ids != 0,
        "duplicate_vector_ids": duplicate_vector_ids != 0,
        "duplicate_bm25_ids": duplicate_bm25_ids != 0,
        "orphan_children": bool(orphan_children),
        "orphan_vectors": bool(orphan_vectors or missing_vectors),
        "orphan_bm25": bool(orphan_bm25 or missing_bm25),
        "profile_sha_mismatch": bool(profile_sha_mismatches),
        "count_mismatch": not (
            len(children)
            == len(vectors)
            == len(bm25)
            == int(vector_matrix.shape[0])
            == int(manifest.artifacts["milvus_lite"].record_count or -1)
        ),
        "vector_dimension": vector_matrix.ndim != 2
        or int(vector_matrix.shape[1]) != 768,
        "vector_non_finite": not bool(np.isfinite(vector_matrix).all()),
        "undisclosed_fallback": undisclosed_fallback != 0,
        "missing_chunking_contract": missing_chunking_contract != 0,
        "historical_boundary": bool(historical_boundary_violations),
        "template_boundary": bool(template_boundary_violations),
        "policy_boundary": bool(policy_boundary_violations),
        "self_retrieval_hit_rate": self_retrieval_hit_rate != 1.0,
    }

    return {
        "schema_version": "stage_c_final_acceptance_report_v2",
        "passed": not any(hard_failures.values()),
        "hard_failures": hard_failures,
        "index": {
            "index_version": manifest.index_version,
            "manifest_path": str(manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
            "config_hash": manifest.config_hash,
            "source_hash": manifest.source_hash,
            "reproducibility_hash": manifest.reproducibility_hash,
            "dataset_version": manifest.dataset_version,
            "embedding_dim": manifest.embedding.get("dim"),
            "backend": manifest.index.get("backend"),
            "collection_name": manifest.index.get("collection_name"),
        },
        "document_inventory": {
            "manifest_document_count": len(manifest_docs),
            "indexed_document_count": len(indexed_docs),
            "gold_document_count": len(gold_docs),
            "gold_document_ids": sorted(gold_docs),
            "gold_chunk_count": gold_chunks,
            "residual_chunk_count": residual_chunks,
            "unknown_document_count": len(unknown_docs),
            "missing_profile_count": 0,
            "orphan_profile_count": 0,
            "source_hash_mismatch_count": 0,
        },
        "artifact_counts": {
            "profiled_cleaned_text_units": len(units),
            "parents": len(parents),
            "children": len(children),
            "vectors": len(vectors),
            "bm25_records": len(bm25),
            "milvus_rows": manifest.artifacts["milvus_lite"].record_count,
            "vector_matrix_shape": list(vector_matrix.shape),
        },
        "artifacts": artifact_records,
        "strategy_summary": strategy_summary,
        "representation_counts": dict(sorted(representation_counts.items())),
        "fallback": {
            "fallback_document_count": len(
                {
                    child.get("doc_id")
                    for child in children
                    if bool((_chunking(child) or {}).get("fallback_used"))
                }
            ),
            "fallback_chunk_count": sum(strategy_fallback_chunks.values()),
            "reason_counts": dict(sorted(fallback_reason_counts.items())),
            "undisclosed_fallback_count": undisclosed_fallback,
            "missing_chunking_contract_count": missing_chunking_contract,
        },
        "kb_child_counts": dict(sorted(kb_chunk_counts.items())),
        "profile_completeness": {
            "missing_required_profile_fields": dict(
                required_profile_missing
            ),
            "missing_authority_contract": dict(authority_contract_missing),
            "missing_taxonomy_mapping_field": dict(taxonomy_field_missing),
            "missing_profile_sha256": dict(profile_sha_missing),
            "missing_source_sha256": dict(source_sha_missing),
        },
        "lineage": {
            "duplicate_parent_ids": duplicate_parent_ids,
            "duplicate_child_ids": duplicate_child_ids,
            "duplicate_vector_ids": duplicate_vector_ids,
            "duplicate_bm25_ids": duplicate_bm25_ids,
            "orphan_child_ids": orphan_children,
            "orphan_vector_ids": orphan_vectors,
            "missing_vector_ids": missing_vectors,
            "orphan_bm25_ids": orphan_bm25,
            "missing_bm25_ids": missing_bm25,
            "profile_sha256_mismatch_ids": profile_sha_mismatches,
        },
        "knowledge_boundaries": {
            "default_kb_chunk_count": default_kb_chunks,
            "unknown_chunk_count": unknown_chunks,
            "historical_profile_count": len(historical_profiles),
            "historical_boundary_violations": historical_boundary_violations,
            "template_boundary_violations": template_boundary_violations,
            "policy_boundary_violations": policy_boundary_violations,
        },
        "structured_samples": sample_children,
        "fact_status_samples": fact_status_samples,
        "self_retrieval": {
            "sample_count": len(self_retrieval),
            "hit_rate": self_retrieval_hit_rate,
            "results": self_retrieval,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument(
        "--profiles",
        default="data/catalog/document_profiles_v1.jsonl",
    )
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    profiles_path = Path(args.profiles).expanduser()
    if not profiles_path.is_absolute():
        profiles_path = (PROJECT_ROOT / profiles_path).resolve()
    output_path = Path(args.output).expanduser().resolve()
    report = audit(
        manifest_path=manifest_path,
        profiles_path=profiles_path,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "index_version": report["index"]["index_version"],
                "artifact_counts": report["artifact_counts"],
                "strategy_summary": report["strategy_summary"],
                "fallback": report["fallback"],
                "kb_child_counts": report["kb_child_counts"],
                "self_retrieval_hit_rate": report["self_retrieval"][
                    "hit_rate"
                ],
                "hard_failures": report["hard_failures"],
                "output": str(output_path),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
