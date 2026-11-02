"""Convert MinerU output to the shared raw parser JSONL contract.

Ported from the user's original ``mineru_adapter.py``.  Both MinerU
``content_list_v2`` (page-nested) and legacy ``content_list`` (flat) formats
are supported.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from document_ingestion.atomic_writer import atomic_write_json, atomic_write_jsonl

SCHEMA_VERSION = "parsed_pdf_unit_v1"
PARSE_VERSION = "mineru_adapter_v1.1_router_port"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _detect_language(text: str) -> str:
    if not text:
        return "unknown"
    chinese = sum("\u4e00" <= char <= "\u9fff" for char in text)
    latin = sum(char.isascii() and char.isalpha() for char in text)
    if chinese and chinese >= latin:
        return "zh"
    if latin:
        return "en"
    return "unknown"


def _quality(text: str, content_type: str) -> tuple[float | None, list[str]]:
    flags: list[str] = []
    if not text:
        if content_type not in {"image_block", "chart_block"}:
            flags.append("empty_text")
        return (0.5 if content_type in {"image_block", "chart_block"} else 0.0), flags
    if len(text) < 5:
        flags.append("short_text")
    replacement_ratio = text.count("\ufffd") / max(len(text), 1)
    if replacement_ratio >= 0.01:
        flags.append("suspected_garbled")
    return max(0.0, round(1.0 - 0.2 * len(flags), 4)), flags


def find_mineru_auto_dir(path: Path) -> Path:
    path = path.resolve(strict=True)
    if path.is_dir() and any(path.glob("*_content_list_v2.json")):
        return path
    if path.is_dir() and any(path.glob("*_content_list.json")):
        return path
    candidates = [candidate.parent for candidate in path.rglob("*_content_list_v2.json")]
    if not candidates:
        candidates = [candidate.parent for candidate in path.rglob("*_content_list.json")]
    if not candidates:
        raise FileNotFoundError(f"No MinerU content_list found under {path}")
    return max(candidates, key=lambda candidate: candidate.stat().st_mtime)


def _choose_file(directory: Path, pattern: str) -> Path | None:
    values = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return values[0] if values else None


def _extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_extract_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if not isinstance(value, dict):
        return ""

    for key in ("text", "content", "title", "math_content", "latex", "html"):
        direct = value.get(key)
        if isinstance(direct, str) and direct.strip():
            return direct.strip()

    parts: list[str] = []
    for key in (
        "title_content",
        "paragraph_content",
        "page_header_content",
        "page_footer_content",
        "image_caption",
        "image_footnote",
        "chart_caption",
        "chart_footnote",
        "table_caption",
        "table_footnote",
        "item_content",
    ):
        if key in value:
            text = _extract_text(value.get(key))
            if text:
                parts.append(text)
    if "list_items" in value:
        items = [_extract_text(item) for item in value.get("list_items") or []]
        items = [item for item in items if item]
        if items:
            parts.append("\n".join(f"- {item}" for item in items))
    nested = value.get("content")
    if nested is not None and not isinstance(nested, str):
        text = _extract_text(nested)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _extract_image_path(item: dict[str, Any]) -> str | None:
    def visit(value: Any) -> str | None:
        if not isinstance(value, dict):
            return None
        for key in ("path", "img_path", "image_path"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        for key in ("image_source", "img_source", "content"):
            candidate = visit(value.get(key))
            if candidate:
                return candidate
        return None

    return visit(item) or visit(item.get("content"))


def _map_type(mineru_type: str, *, text_level: Any = None) -> str:
    if mineru_type == "text" and text_level not in {None, 0, ""}:
        return "title"
    return {
        "title": "title",
        "text": "paragraph",
        "paragraph": "paragraph",
        "index": "list",
        "list": "list",
        "page_header": "header",
        "header": "header",
        "page_footer": "footer",
        "footer": "footer",
        "image": "image_block",
        "chart": "chart_block",
        "equation": "formula_block",
        "equation_interline": "formula_block",
        "table": "table_block",
        "table_body": "table_block",
        "table_block": "table_block",
    }.get(mineru_type, "other")


def _base_record(
    *,
    doc_id: str,
    source_pdf: Path,
    page_no: int,
    order: int,
    mineru_type: str,
    text_level: Any = None,
) -> dict[str, Any]:
    content_type = _map_type(mineru_type, text_level=text_level)
    parse_unit_id = f"{doc_id}_p{page_no:04d}_u{order:04d}_{content_type}"
    return {
        "schema_version": SCHEMA_VERSION,
        "parse_unit_id": parse_unit_id,
        "doc_id": doc_id,
        "source_uri": str(source_pdf.resolve(strict=True)),
        "source_name": source_pdf.name,
        "source_format": "pdf",
        "page_no": page_no,
        "block_id": parse_unit_id,
        "content_type": content_type,
        "text": "",
        "text_length": 0,
        "image_uri": None,
        "table_uri": None,
        "parser": "mineru",
        "parse_version": PARSE_VERSION,
        "ocr_used": False,
        "quality_score": None,
        "quality_flags": [],
        "created_at": _now_iso(),
        "extra": {
            "mineru_type": mineru_type,
            "unit_order_in_page": order,
        },
    }


def _convert_v2_item(
    item: dict[str, Any],
    *,
    auto_dir: Path,
    source_pdf: Path,
    doc_id: str,
    page_no: int,
    order: int,
) -> dict[str, Any]:
    mineru_type = str(item.get("type") or "other")
    content = item.get("content")
    level = content.get("level") if isinstance(content, dict) else None
    record = _base_record(
        doc_id=doc_id,
        source_pdf=source_pdf,
        page_no=page_no,
        order=order,
        mineru_type=mineru_type,
        text_level=level,
    )
    text = _normalize_text(_extract_text(content))
    image_path = _extract_image_path(item)
    if image_path:
        candidate = (auto_dir / image_path).resolve()
        if record["content_type"] == "table_block":
            record["table_uri"] = str(candidate)
        else:
            record["image_uri"] = str(candidate)
    record["text"] = text
    record["text_length"] = len(text)
    score, flags = _quality(text, record["content_type"])
    record["quality_score"] = score
    record["quality_flags"] = flags
    record["extra"].update(
        {
            "bbox": item.get("bbox"),
            "language": _detect_language(text),
            "title_level": level,
        }
    )
    return record


def _convert_v1_item(
    item: dict[str, Any],
    *,
    auto_dir: Path,
    source_pdf: Path,
    doc_id: str,
    order_by_page: dict[int, int],
) -> dict[str, Any]:
    page_no = int(item.get("page_idx", 0)) + 1
    order_by_page[page_no] += 1
    text_level = item.get("text_level")
    mineru_type = str(item.get("type") or "other")
    record = _base_record(
        doc_id=doc_id,
        source_pdf=source_pdf,
        page_no=page_no,
        order=order_by_page[page_no],
        mineru_type=mineru_type,
        text_level=text_level,
    )
    text = _normalize_text(item.get("text") if "text" in item else item.get("content"))
    image_path = _extract_image_path(item)
    if image_path:
        candidate = str((auto_dir / image_path).resolve())
        if record["content_type"] == "table_block":
            record["table_uri"] = candidate
        else:
            record["image_uri"] = candidate
    record["text"] = text
    record["text_length"] = len(text)
    score, flags = _quality(text, record["content_type"])
    record["quality_score"] = score
    record["quality_flags"] = flags
    record["extra"].update(
        {
            "bbox": item.get("bbox"),
            "language": _detect_language(text),
            "text_level": text_level,
            "title_level": text_level if record["content_type"] == "title" else None,
        }
    )
    return record


def convert_mineru_output(
    *,
    mineru_dir: Path,
    base_dir: Path,
    doc_id: str,
    source_pdf: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    auto_dir = find_mineru_auto_dir(mineru_dir)
    content_v2 = _choose_file(auto_dir, "*_content_list_v2.json")
    content_v1 = _choose_file(auto_dir, "*_content_list.json")
    chosen = content_v2 or content_v1
    if chosen is None:
        raise FileNotFoundError(f"No MinerU content list found in {auto_dir}")

    base_dir = base_dir.resolve()
    parsed_path = base_dir / "parsed_jsonl" / f"{doc_id}.parsed.jsonl"
    profile_path = base_dir / "profile" / f"{doc_id}.profile.json"
    adapter_eval_path = base_dir / "eval_report" / f"{doc_id}.adapter_eval.json"
    if parsed_path.exists() and not overwrite:
        raise FileExistsError(f"Output exists: {parsed_path}")

    with chosen.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    records: list[dict[str, Any]] = []
    if chosen == content_v2:
        if not isinstance(data, list):
            raise TypeError("MinerU content_list_v2 must be a list of pages")
        for page_index, page_items in enumerate(data, 1):
            if not isinstance(page_items, list):
                continue
            for order, item in enumerate(page_items, 1):
                if isinstance(item, dict):
                    records.append(
                        _convert_v2_item(
                            item,
                            auto_dir=auto_dir,
                            source_pdf=source_pdf,
                            doc_id=doc_id,
                            page_no=page_index,
                            order=order,
                        )
                    )
        page_count = len(data)
        content_format = "content_list_v2"
    else:
        if not isinstance(data, list):
            raise TypeError("MinerU content_list must be a list")
        orders: dict[int, int] = defaultdict(int)
        for item in data:
            if isinstance(item, dict):
                records.append(
                    _convert_v1_item(
                        item,
                        auto_dir=auto_dir,
                        source_pdf=source_pdf,
                        doc_id=doc_id,
                        order_by_page=orders,
                    )
                )
        page_count = max((record["page_no"] for record in records), default=0)
        content_format = "content_list_v1"

    atomic_write_jsonl(parsed_path, records, root=base_dir)
    content_counts = Counter(record["content_type"] for record in records)
    page_text: dict[int, int] = defaultdict(int)
    for record in records:
        if record["content_type"] not in {"header", "footer", "image_block", "chart_block"}:
            page_text[record["page_no"]] += record["text_length"]
    empty_pages = [page for page in range(1, page_count + 1) if page_text.get(page, 0) < 30]
    adapter_eval = {
        "schema_version": "parse_eval_v1",
        "doc_id": doc_id,
        "page_count": page_count,
        "total_records": len(records),
        "content_type_counts": dict(content_counts),
        "text_coverage_ratio": (
            round((page_count - len(empty_pages)) / page_count, 4) if page_count else 0.0
        ),
        "empty_text_pages": empty_pages,
        "quality_level": (
            "need_ocr_or_manual_check"
            if page_count and len(empty_pages) / page_count > 0.5
            else "pass_with_warnings"
            if empty_pages
            else "pass"
        ),
        "created_at": _now_iso(),
    }
    atomic_write_json(adapter_eval_path, adapter_eval, root=base_dir)

    profile = {
        "schema_version": "pdf_profile_v1",
        "doc_id": doc_id,
        "source_name": source_pdf.name,
        "source_uri": str(source_pdf.resolve(strict=True)),
        "source_format": "pdf",
        "file_size": source_pdf.stat().st_size,
        "parser": "mineru",
        "parse_version": PARSE_VERSION,
        "mineru_auto_dir": str(auto_dir),
        "mineru_content_format": content_format,
        "page_count": page_count,
        "total_records": len(records),
        "content_type_counts": dict(content_counts),
        "mineru_content_list": str(chosen),
        "mineru_images_dir": str(auto_dir / "images") if (auto_dir / "images").is_dir() else None,
        "parsed_jsonl": str(parsed_path),
        "adapter_eval_report": str(adapter_eval_path),
        "quality_level": adapter_eval["quality_level"],
        "created_at": _now_iso(),
    }
    atomic_write_json(profile_path, profile, root=base_dir)
    return {
        "parsed_jsonl": str(parsed_path),
        "profile": str(profile_path),
        "adapter_eval": str(adapter_eval_path),
        "record_count": len(records),
        "page_count": page_count,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert MinerU output to parsed_pdf_unit_v1 JSONL")
    parser.add_argument("--mineru-dir", type=Path, required=True)
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--doc-id", required=True)
    parser.add_argument("--source-pdf", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = convert_mineru_output(
        mineru_dir=args.mineru_dir,
        base_dir=args.base_dir,
        doc_id=args.doc_id,
        source_pdf=args.source_pdf,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
