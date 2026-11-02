"""Render PDF pages and run PaddleOCR as the fallback parser.

Heavy dependencies are imported lazily so the Word-only environment can still
import the router and execute dry-runs.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from document_ingestion.atomic_writer import atomic_write_json, atomic_write_jsonl
from document_ingestion.hashing import sha256_file

SCHEMA_VERSION = "parsed_pdf_unit_v1"
PARSER_NAME = "paddleocr"
PARSE_VERSION = "paddleocr_adapter_v1.1_router_port"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_dependencies() -> tuple[Any, Any]:
    try:
        import fitz  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError("Missing PDF dependency: PyMuPDF (import name: fitz)") from exc
    try:
        from paddleocr import PaddleOCR  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError("Missing OCR dependency: paddleocr") from exc
    return fitz, PaddleOCR


def _ensure_dirs(base_dir: Path, doc_id: str) -> dict[str, Path]:
    paths = {
        "doc": base_dir / "paddleocr_output" / doc_id,
        "pages": base_dir / "paddleocr_output" / doc_id / "page_images",
        "parsed": base_dir / "parsed_jsonl",
        "profile": base_dir / "profile",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def render_pdf_pages(
    input_pdf: Path,
    image_dir: Path,
    *,
    dpi: int = 200,
    overwrite: bool = False,
) -> list[Path]:
    fitz, _ = _load_dependencies()
    document = fitz.open(str(input_pdf))
    try:
        matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        outputs: list[Path] = []
        for page_index in range(document.page_count):
            output = image_dir / f"page_{page_index + 1:04d}.png"
            outputs.append(output)
            if output.exists() and not overwrite:
                continue
            page = document.load_page(page_index)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            pixmap.save(str(output))
        return outputs
    finally:
        document.close()


def normalize_ocr_result(result: Any) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []

    def to_list(value: Any) -> Any:
        try:
            return value.tolist() if hasattr(value, "tolist") else value
        except Exception:
            return value

    def to_float(value: Any) -> float | None:
        try:
            return float(value)
        except Exception:
            return None

    def visit(value: Any) -> None:
        if value is None:
            return
        if hasattr(value, "json"):
            try:
                visit(value.json)
                return
            except Exception:
                pass
        if hasattr(value, "to_dict"):
            try:
                visit(value.to_dict())
                return
            except Exception:
                pass
        if isinstance(value, dict):
            for wrapper in ("res", "prunedResult"):
                if wrapper in value:
                    visit(value[wrapper])
                    return
            if "rec_texts" in value:
                texts = value.get("rec_texts") or []
                scores = to_list(value.get("rec_scores") or [])
                boxes = to_list(
                    value.get("rec_boxes")
                    or value.get("rec_polys")
                    or value.get("dt_polys")
                    or []
                )
                for index, raw_text in enumerate(texts):
                    text = str(raw_text).strip()
                    if not text:
                        continue
                    lines.append(
                        {
                            "text": text,
                            "score": to_float(scores[index])
                            if isinstance(scores, (list, tuple)) and index < len(scores)
                            else None,
                            "bbox": boxes[index]
                            if isinstance(boxes, (list, tuple)) and index < len(boxes)
                            else None,
                        }
                    )
                return
            for nested in value.values():
                visit(nested)
            return
        if isinstance(value, (list, tuple)):
            if (
                len(value) >= 2
                and isinstance(value[1], (list, tuple))
                and value[1]
                and isinstance(value[1][0], str)
            ):
                text = str(value[1][0]).strip()
                if text:
                    lines.append(
                        {
                            "text": text,
                            "score": to_float(value[1][1]) if len(value[1]) > 1 else None,
                            "bbox": to_list(value[0]),
                        }
                    )
                return
            for nested in value:
                visit(nested)

    visit(result)
    return lines


def run_paddleocr_on_images(
    image_paths: list[Path],
    *,
    lang: str = "ch",
    use_gpu: bool = False,
) -> dict[int, list[dict[str, Any]]]:
    _, PaddleOCR = _load_dependencies()
    try:
        ocr = PaddleOCR(use_textline_orientation=True, lang=lang)
    except Exception:
        try:
            ocr = PaddleOCR(use_angle_cls=True, lang=lang, use_gpu=use_gpu)
        except Exception:
            ocr = PaddleOCR(lang=lang)

    pages: dict[int, list[dict[str, Any]]] = {}
    for image_path in image_paths:
        page_no = int(image_path.stem.rsplit("_", 1)[-1])
        try:
            lines: list[dict[str, Any]] = []
            if hasattr(ocr, "predict"):
                lines = normalize_ocr_result(ocr.predict(str(image_path)))
            if not lines and hasattr(ocr, "ocr"):
                try:
                    raw = ocr.ocr(str(image_path), cls=True)
                except TypeError:
                    raw = ocr.ocr(str(image_path))
                lines = normalize_ocr_result(raw)
            pages[page_no] = lines
            print(
                f"[PaddleOCR] page={page_no}, lines={len(lines)}, "
                f"text_len={sum(len(str(item.get('text') or '')) for item in lines)}",
                flush=True,
            )
        except Exception as exc:
            pages[page_no] = [
                {"text": "", "score": None, "bbox": None, "error": str(exc)}
            ]
            print(f"[PaddleOCR][ERROR] page={page_no}: {exc}", flush=True)
    return pages


def _build_records(
    *,
    input_pdf: Path,
    doc_id: str,
    image_paths: list[Path],
    page_lines: dict[int, list[dict[str, Any]]],
    dpi: int,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    created_at = _now_iso()
    for image_path in image_paths:
        page_no = int(image_path.stem.rsplit("_", 1)[-1])
        raw_lines = page_lines.get(page_no, [])
        clean_lines = [
            {**line, "text": str(line.get("text") or "").strip()}
            for line in raw_lines
            if str(line.get("text") or "").strip()
        ]
        text = "\n".join(line["text"] for line in clean_lines)
        scores = [
            float(line["score"])
            for line in clean_lines
            if isinstance(line.get("score"), (int, float))
        ]
        quality_flags: list[str] = []
        if not text:
            quality_flags.append("ocr_empty_text")
        if any("error" in line for line in raw_lines):
            quality_flags.append("ocr_error")
        parse_unit_id = f"{doc_id}_p{page_no:04d}_ocr_text"
        records.append(
            {
                "schema_version": SCHEMA_VERSION,
                "parse_unit_id": parse_unit_id,
                "doc_id": doc_id,
                "source_uri": str(input_pdf.resolve(strict=True)),
                "source_name": input_pdf.name,
                "source_format": "pdf",
                "page_no": page_no,
                "block_id": parse_unit_id,
                "content_type": "ocr_page_text",
                "text": text,
                "text_length": len(text),
                "image_uri": str(image_path.resolve()),
                "table_uri": None,
                "parser": PARSER_NAME,
                "parse_version": PARSE_VERSION,
                "ocr_used": True,
                "quality_score": round(sum(scores) / len(scores), 4) if scores else None,
                "quality_flags": quality_flags,
                "created_at": created_at,
                "extra": {
                    "dpi": dpi,
                    "line_count": len(clean_lines),
                    "ocr_lines": clean_lines,
                },
            }
        )
    return records


def parse_with_paddleocr(
    *,
    input_pdf: Path,
    base_dir: Path,
    doc_id: str,
    dpi: int = 200,
    lang: str = "ch",
    use_gpu: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    input_pdf = input_pdf.resolve(strict=True)
    base_dir = base_dir.resolve()
    paths = _ensure_dirs(base_dir, doc_id)
    parsed_path = paths["parsed"] / f"{doc_id}.parsed.jsonl"
    profile_path = paths["profile"] / f"{doc_id}.profile.json"
    if parsed_path.exists() and profile_path.exists() and not overwrite:
        return {
            "status": "skipped_existing",
            "parsed_jsonl": str(parsed_path),
            "profile": str(profile_path),
        }

    print(f"[PaddleOCR] render pages: {input_pdf.name}", flush=True)
    image_paths = render_pdf_pages(
        input_pdf, paths["pages"], dpi=dpi, overwrite=overwrite
    )
    print(f"[PaddleOCR] OCR pages: {len(image_paths)}", flush=True)
    page_lines = run_paddleocr_on_images(image_paths, lang=lang, use_gpu=use_gpu)
    records = _build_records(
        input_pdf=input_pdf,
        doc_id=doc_id,
        image_paths=image_paths,
        page_lines=page_lines,
        dpi=dpi,
    )
    atomic_write_jsonl(parsed_path, records, root=base_dir)
    empty_pages = [record["page_no"] for record in records if not record["text"]]
    profile = {
        "schema_version": "pdf_profile_v1",
        "doc_id": doc_id,
        "file_name": input_pdf.name,
        "source_uri": str(input_pdf),
        "source_format": "pdf",
        "file_size": input_pdf.stat().st_size,
        "file_sha256": sha256_file(input_pdf),
        "page_count": len(image_paths),
        "parser": PARSER_NAME,
        "parse_version": PARSE_VERSION,
        "ocr_required": True,
        "ocr_used": True,
        "dpi": dpi,
        "total_records": len(records),
        "total_text_length": sum(record["text_length"] for record in records),
        "empty_text_pages": empty_pages,
        "parsed_jsonl": str(parsed_path),
        "created_at": _now_iso(),
    }
    atomic_write_json(profile_path, profile, root=base_dir)
    return {
        "status": "success",
        "parsed_jsonl": str(parsed_path),
        "profile": str(profile_path),
        "page_count": len(image_paths),
        "record_count": len(records),
        "empty_text_pages": empty_pages,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PaddleOCR PDF fallback adapter")
    parser.add_argument("--input-pdf", type=Path, required=True)
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--doc-id", required=True)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--lang", default="ch")
    parser.add_argument("--use-gpu", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = parse_with_paddleocr(
        input_pdf=args.input_pdf,
        base_dir=args.base_dir,
        doc_id=args.doc_id,
        dpi=args.dpi,
        lang=args.lang,
        use_gpu=args.use_gpu,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
