"""Evaluate raw PDF parser JSONL and decide whether OCR fallback is required.

This is a package-safe port of the user's original ``parse_eval.py``.  It
keeps the same decision vocabulary and report structure used by the previous
PDF pipeline.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from document_ingestion.atomic_writer import atomic_write_json

EVAL_VERSION = "parse_eval_v1"

COMMON_REQUIRED_FIELDS = (
    "schema_version",
    "parse_unit_id",
    "doc_id",
    "source_uri",
    "source_name",
    "source_format",
    "page_no",
    "block_id",
    "content_type",
    "parser",
    "parse_version",
)
TEXT_REQUIRED_TYPES = {
    "title",
    "paragraph",
    "list",
    "table_block",
    "formula_block",
    "chart_block",
    "ocr_page_text",
    "page_text",
    "text_block",
    "header",
    "footer",
}
BODY_TEXT_TYPES = {
    "title",
    "paragraph",
    "list",
    "table_block",
    "formula_block",
    "chart_block",
    "ocr_page_text",
    "page_text",
    "text_block",
}


def _load_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}


def _load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    bad_lines: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            raw = line.strip()
            if not raw:
                continue
            try:
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise TypeError("JSONL record must be an object")
                records.append(value)
            except Exception as exc:
                bad_lines.append(
                    {"line_no": line_no, "error": str(exc), "prefix": raw[:200]}
                )
    return records, bad_lines


def _safe_exists(uri: Any, base_dir: Path | None) -> bool:
    if not isinstance(uri, str) or not uri:
        return False
    if uri.startswith(("hdfs://", "s3://", "minio://", "http://", "https://")):
        return True
    candidate = Path(uri)
    if candidate.exists():
        return True
    return bool(base_dir is not None and (base_dir / candidate).exists())


def _page_count(profile: dict[str, Any], records: Iterable[dict[str, Any]]) -> int | None:
    for key in ("page_count", "total_pages"):
        value = profile.get(key)
        if isinstance(value, int) and value > 0:
            return value
    pages = [record.get("page_no") for record in records]
    valid = [page for page in pages if isinstance(page, int) and page > 0]
    return max(valid) if valid else None


def _text_length(record: dict[str, Any]) -> int:
    value = record.get("text_length")
    try:
        return int(value)
    except Exception:
        return len(str(record.get("text") or ""))


def _text_stats(lengths: list[int]) -> dict[str, float | int]:
    if not lengths:
        return {"count": 0, "min": 0, "max": 0, "avg": 0, "median": 0}
    return {
        "count": len(lengths),
        "min": min(lengths),
        "max": max(lengths),
        "avg": round(sum(lengths) / len(lengths), 2),
        "median": round(float(statistics.median(lengths)), 2),
    }


def _missing_fields(record: dict[str, Any]) -> list[str]:
    missing = [
        field
        for field in COMMON_REQUIRED_FIELDS
        if field not in record or record.get(field) in {None, ""}
    ]
    content_type = record.get("content_type")
    if "text" not in record or (
        content_type in TEXT_REQUIRED_TYPES and record.get("text") is None
    ):
        missing.append("text")
    if "text_length" not in record or record.get("text_length") is None:
        missing.append("text_length")
    if content_type == "image_block" and not record.get("image_uri"):
        missing.append("image_uri")
    if content_type == "table_block" and not (
        record.get("text") or record.get("table_uri")
    ):
        missing.append("text_or_table_uri")
    return list(dict.fromkeys(missing))


def evaluate_parse(
    parsed_jsonl: Path,
    profile_path: Path | None,
    output_path: Path,
    base_dir: Path | None,
    min_page_text_len: int = 30,
) -> dict[str, Any]:
    records, bad_lines = _load_jsonl(parsed_jsonl)
    profile = _load_json(profile_path)
    page_count = _page_count(profile, records)

    content_counts: Counter[str] = Counter()
    parser_counts: Counter[str] = Counter()
    page_records: dict[int, list[dict[str, Any]]] = defaultdict(list)
    page_text_length: dict[int, int] = defaultdict(int)
    page_body_text_length: dict[int, int] = defaultdict(int)
    all_text_lengths: list[int] = []
    body_text_lengths: list[int] = []
    missing_required: list[dict[str, Any]] = []
    duplicate_ids: list[str] = []
    seen_ids: set[str] = set()
    invalid_pages: list[dict[str, Any]] = []
    image_records: list[dict[str, Any]] = []
    table_records: list[dict[str, Any]] = []
    parse_error_records: list[dict[str, Any]] = []

    for record_index, record in enumerate(records, 1):
        content_type = str(record.get("content_type") or "unknown")
        content_counts[content_type] += 1
        parser_counts[str(record.get("parser") or "unknown")] += 1

        parse_unit_id = record.get("parse_unit_id")
        if isinstance(parse_unit_id, str) and parse_unit_id:
            if parse_unit_id in seen_ids:
                duplicate_ids.append(parse_unit_id)
            seen_ids.add(parse_unit_id)

        missing = _missing_fields(record)
        if missing:
            missing_required.append(
                {
                    "record_index": record_index,
                    "parse_unit_id": parse_unit_id,
                    "content_type": content_type,
                    "missing_fields": missing,
                }
            )

        page_no = record.get("page_no")
        if isinstance(page_no, int) and page_no > 0:
            page_records[page_no].append(record)
            if page_count is not None and page_no > page_count:
                invalid_pages.append(
                    {
                        "parse_unit_id": parse_unit_id,
                        "page_no": page_no,
                        "reason": "page_no_out_of_range",
                    }
                )
        else:
            invalid_pages.append(
                {
                    "parse_unit_id": parse_unit_id,
                    "page_no": page_no,
                    "reason": "page_no_not_positive_int",
                }
            )

        text_length = _text_length(record)
        if content_type in TEXT_REQUIRED_TYPES:
            all_text_lengths.append(text_length)
            if isinstance(page_no, int):
                page_text_length[page_no] += text_length
        if content_type in BODY_TEXT_TYPES:
            body_text_lengths.append(text_length)
            if isinstance(page_no, int):
                page_body_text_length[page_no] += text_length
        if content_type == "image_block":
            image_records.append(record)
        elif content_type == "table_block":
            table_records.append(record)
        elif content_type == "parse_error":
            parse_error_records.append(record)

    all_pages = set(range(1, page_count + 1)) if page_count else set(page_records)
    covered_pages = set(page_records)
    text_pages = {
        page for page, length in page_text_length.items() if length >= min_page_text_len
    }
    body_text_pages = {
        page
        for page, length in page_body_text_length.items()
        if length >= min_page_text_len
    }
    missing_pages = sorted(all_pages - covered_pages)
    empty_text_pages = sorted(all_pages - text_pages)
    empty_body_text_pages = sorted(all_pages - body_text_pages)

    denominator = len(all_pages)
    page_coverage_ratio = (
        round(len(covered_pages & all_pages) / denominator, 4) if denominator else 0.0
    )
    text_coverage_ratio = (
        round(len(text_pages & all_pages) / denominator, 4) if denominator else 0.0
    )
    body_text_coverage_ratio = (
        round(len(body_text_pages & all_pages) / denominator, 4)
        if denominator
        else 0.0
    )

    image_uris = [record.get("image_uri") for record in image_records if record.get("image_uri")]
    image_exists = sum(_safe_exists(uri, base_dir) for uri in image_uris)
    image_ratio = round(image_exists / len(image_uris), 4) if image_uris else None
    table_uris = [record.get("table_uri") for record in table_records if record.get("table_uri")]
    table_exists = sum(_safe_exists(uri, base_dir) for uri in table_uris)
    table_ratio = round(table_exists / len(table_uris), 4) if table_uris else None

    critical_fields = set(COMMON_REQUIRED_FIELDS)
    critical_missing = [
        item
        for item in missing_required
        if set(item["missing_fields"]) & critical_fields
    ]
    noncritical_missing = [item for item in missing_required if item not in critical_missing]

    fail_reasons: list[str] = []
    warning_reasons: list[str] = []
    need_ocr_reasons: list[str] = []
    need_review_reasons: list[str] = []
    if bad_lines:
        fail_reasons.append("jsonl_invalid")
    if duplicate_ids:
        fail_reasons.append("duplicate_parse_unit_id")
    if invalid_pages:
        fail_reasons.append("invalid_page_no")
    if critical_missing:
        fail_reasons.append("critical_required_fields_missing")
    if noncritical_missing:
        warning_reasons.append("noncritical_fields_missing")
    if page_count and page_coverage_ratio < 0.95:
        need_review_reasons.append("low_page_coverage")
    if page_count and text_coverage_ratio < 0.5:
        need_ocr_reasons.append("low_text_coverage")
    if page_count and len(empty_text_pages) / max(page_count, 1) > 0.5:
        need_ocr_reasons.append("too_many_empty_text_pages")
    if parse_error_records:
        need_review_reasons.append("parse_errors_found")
    if image_ratio is not None and image_ratio < 0.98:
        warning_reasons.append("some_image_files_missing")
    if table_ratio is not None and table_ratio < 0.98:
        warning_reasons.append("some_table_files_missing")
    if page_count and 0 < len(empty_text_pages) <= max(3, int(page_count * 0.2)):
        warning_reasons.append("some_empty_text_pages")
    if records:
        paragraph_ratio = content_counts.get("paragraph", 0) / len(records)
        media_ratio = content_counts.get("image_block", 0) / len(records)
        if paragraph_ratio < 0.1 and media_ratio > 0.5:
            need_ocr_reasons.append("mostly_images_with_little_text")

    if fail_reasons:
        quality_level = "fail"
    elif need_ocr_reasons:
        quality_level = "need_ocr"
    elif need_review_reasons:
        quality_level = "need_review"
    elif warning_reasons:
        quality_level = "pass_with_warnings"
    else:
        quality_level = "pass"
    can_enter_spark_cleaning = quality_level in {
        "pass",
        "pass_with_warnings",
        "need_review",
    }

    report = {
        "eval_version": EVAL_VERSION,
        "parsed_jsonl": str(parsed_jsonl),
        "profile": str(profile_path) if profile_path else None,
        "doc_id": profile.get("doc_id") or (records[0].get("doc_id") if records else None),
        "source_name": profile.get("file_name") or profile.get("source_name"),
        "source_uri": profile.get("source_uri"),
        "parser": profile.get("parser") or dict(parser_counts),
        "basic_validity": {
            "jsonl_valid": not bad_lines,
            "bad_line_count": len(bad_lines),
            "bad_lines_sample": bad_lines[:10],
            "total_records": len(records),
            "required_fields_missing_count": len(missing_required),
            "critical_required_fields_missing_count": len(critical_missing),
            "noncritical_required_fields_missing_count": len(noncritical_missing),
            "required_fields_missing_sample": missing_required[:20],
            "duplicate_parse_unit_id_count": len(duplicate_ids),
            "duplicate_parse_unit_id_sample": duplicate_ids[:10],
            "invalid_page_record_count": len(invalid_pages),
            "invalid_page_record_sample": invalid_pages[:10],
        },
        "coverage": {
            "page_count": page_count,
            "covered_pages": len(covered_pages & all_pages),
            "page_coverage_ratio": page_coverage_ratio,
            "text_coverage_ratio": text_coverage_ratio,
            "body_text_coverage_ratio": body_text_coverage_ratio,
            "missing_pages": missing_pages,
            "empty_text_pages": empty_text_pages,
            "empty_body_text_pages": empty_body_text_pages,
            "ocr_candidate_pages": empty_text_pages,
            "min_page_text_len": min_page_text_len,
        },
        "content_structure": {
            "content_type_counts": dict(content_counts),
            "parser_counts": dict(parser_counts),
        },
        "text_quality": {
            "all_text_unit_stats": _text_stats(all_text_lengths),
            "body_text_unit_stats": _text_stats(body_text_lengths),
        },
        "media_quality": {
            "image_block_count": len(image_records),
            "image_uri_count": len(image_uris),
            "image_file_exists_ratio": image_ratio,
            "table_block_count": len(table_records),
            "table_uri_count": len(table_uris),
            "table_file_exists_ratio": table_ratio,
        },
        "traceability": {
            field: round(
                sum(record.get(field) not in {None, ""} for record in records)
                / len(records),
                4,
            )
            if records
            else 0.0
            for field in (
                "doc_id",
                "source_uri",
                "source_name",
                "source_format",
                "page_no",
                "block_id",
                "parse_unit_id",
            )
        },
        "errors": {"parse_error_count": len(parse_error_records)},
        "quality_decision": {
            "quality_level": quality_level,
            "fail_reasons": fail_reasons,
            "warning_reasons": warning_reasons,
            "need_ocr_reasons": need_ocr_reasons,
            "need_review_reasons": need_review_reasons,
            "can_enter_spark_cleaning": can_enter_spark_cleaning,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_path, report, root=output_path.parent.parent)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate parsed_pdf_unit_v1 JSONL quality")
    parser.add_argument("--parsed-jsonl", type=Path, required=True)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-dir", type=Path)
    parser.add_argument("--min-page-text-len", type=int, default=30)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = evaluate_parse(
        parsed_jsonl=args.parsed_jsonl.resolve(strict=True),
        profile_path=args.profile.resolve(strict=True) if args.profile else None,
        output_path=args.output.resolve(),
        base_dir=args.base_dir.resolve() if args.base_dir else None,
        min_page_text_len=args.min_page_text_len,
    )
    decision = report["quality_decision"]
    print("parse eval done")
    print("quality_level:", decision["quality_level"])
    print("can_enter_spark_cleaning:", decision["can_enter_spark_cleaning"])
    print("need_ocr_reasons:", decision["need_ocr_reasons"])
    print("output:", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
