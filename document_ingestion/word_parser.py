"""Microsoft Word COM parser preserving page numbers and Range.Start order."""

from __future__ import annotations

import gc
import re
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .asset_extractor import AssetExtractor
from .exceptions import PageNumberUnavailableError, ParseError
from .quality_checker import language_of, unit_quality
from .schemas import ParsedContentUnitV1
from .table_serializer import TableSerializer
from .text_normalizer import normalize_word_text
from .word_application import WordApplication

WD_ACTIVE_END_PAGE_NUMBER = 3
WD_WITHIN_TABLE = 12
WD_LIST_NO_NUMBERING = 0
WD_OUTLINE_LEVEL_BODY_TEXT = 10
WD_STATISTIC_PAGES = 2
WD_GO_TO_PAGE = 1
WD_GO_TO_ABSOLUTE = 1

_KIND_RANK = {"formula": 0, "table": 1, "inline_shape": 2, "shape": 3, "paragraph": 4}


@dataclass(slots=True)
class _Candidate:
    kind: str
    content_type: str
    word_object: Any
    range_object: Any
    range_start: int
    range_end: int
    text: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    page_no: int = 0
    page_end: int = 0
    story_rank: int = 1
    order_start: int = 0


@dataclass(frozen=True, slots=True)
class _PageIndex:
    starts: tuple[int, ...]
    pages: tuple[int, ...]

    def page_at(self, position: int) -> int:
        index = bisect_right(self.starts, position) - 1
        if index < 0:
            index = 0
        return self.pages[index]


class WordParser:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root.resolve()
        self.tables = TableSerializer(self.output_root)
        self.assets = AssetExtractor(self.output_root)

    def parse(
        self,
        parse_path: Path,
        *,
        source_path: Path,
        source_format: str,
        source_sha256: str,
        doc_id: str,
        created_at: str,
        word: WordApplication,
    ) -> list[ParsedContentUnitV1]:
        del source_sha256  # identity is carried by doc_id and the manifest, not the top-level schema
        source_path = source_path.resolve(strict=True)
        try:
            units: list[ParsedContentUnitV1]
            with word.open_document(parse_path.resolve(strict=True)) as document:
                candidates: list[_Candidate] = []
                try:
                    try:
                        document.Repaginate()
                    except Exception:
                        pass
                    candidates = self._collect_candidates(document)
                    if not candidates:
                        raise ParseError(f"no parseable Word content in {source_path.name}")
                    units = self._materialize(
                        candidates,
                        document=document,
                        doc_id=doc_id,
                        source_path=source_path,
                        source_format=source_format,
                        created_at=created_at,
                    )
                finally:
                    _release_candidates(candidates)
            return units
        except (ParseError, PageNumberUnavailableError):
            raise
        except Exception as exc:
            raise ParseError(f"Word parsing failed for {source_path.name}: {exc}") from exc

    def _collect_candidates(self, document: Any) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        tables = list(_iter_collection(document.Tables))
        table_ranges = sorted(
            (int(table.Range.Start), int(table.Range.End))
            for table in tables
            if _safe_int(table, "NestingLevel") in {None, 1}
        )
        for paragraph in _iter_body_paragraphs(document, table_ranges):
            paragraph_range = paragraph.Range
            paragraph_start = int(paragraph_range.Start)
            if any(start <= paragraph_start < end for start, end in table_ranges):
                continue
            text, text_normalizations = normalize_word_text(str(paragraph_range.Text))
            if not text:
                continue
            content_type, paragraph_extra, text = _paragraph_semantics(paragraph, paragraph_range, text)
            if text_normalizations:
                paragraph_extra["text_normalizations"] = text_normalizations
            candidates.append(_candidate("paragraph", content_type, paragraph, paragraph_range, text, paragraph_extra))

        for table in tables:
            try:
                if int(table.NestingLevel) > 1:
                    continue
            except Exception:
                pass
            candidates.append(_candidate("table", "table_block", table, table.Range))

        for inline_shape in _iter_collection(document.InlineShapes):
            is_chart = _has_chart(inline_shape)
            content_type = "chart_block" if is_chart else "image_block"
            shape_text, text_normalizations = _shape_text(inline_shape)
            shape_extra: dict[str, Any] = {"anchor_type": "inline", "is_chart": is_chart}
            if text_normalizations:
                shape_extra["text_normalizations"] = text_normalizations
            candidates.append(
                _candidate(
                    "inline_shape",
                    content_type,
                    inline_shape,
                    inline_shape.Range,
                    shape_text,
                    shape_extra,
                )
            )

        for shape in _iter_collection(document.Shapes):
            is_chart = _has_chart(shape)
            content_type = "chart_block" if is_chart else "image_block"
            shape_text, text_normalizations = _shape_text(shape)
            shape_extra = {"anchor_type": "floating", "is_chart": is_chart}
            if text_normalizations:
                shape_extra["text_normalizations"] = text_normalizations
            candidates.append(
                _candidate(
                    "shape",
                    content_type,
                    shape,
                    shape.Anchor,
                    shape_text,
                    shape_extra,
                )
            )

        for formula in _iter_collection(document.OMaths):
            formula_range = formula.Range
            formula_text, text_normalizations = normalize_word_text(str(formula_range.Text))
            formula_extra: dict[str, Any] = {"formula_format": "word_omath_linear"}
            if text_normalizations:
                formula_extra["text_normalizations"] = text_normalizations
            candidates.append(
                _candidate(
                    "formula",
                    "formula_block",
                    formula,
                    formula_range,
                    formula_text,
                    formula_extra,
                )
            )

        self._collect_headers_and_footers(document, candidates)
        return candidates

    def _collect_headers_and_footers(self, document: Any, candidates: list[_Candidate]) -> None:
        for section_index, section in enumerate(_iter_collection(document.Sections), 1):
            section_range = section.Range
            order_start = int(section_range.Start)
            for content_type, collection_name, story_rank in (
                ("header", "Headers", 0),
                ("footer", "Footers", 2),
            ):
                collection = getattr(section, collection_name)
                for story in _iter_collection(collection):
                    try:
                        if story.Exists is False:
                            continue
                    except Exception:
                        pass
                    story_range = story.Range
                    text, text_normalizations = normalize_word_text(str(story_range.Text))
                    if not text:
                        continue
                    story_extra: dict[str, Any] = {
                        "section_index": section_index,
                        "story_type": _safe_scalar(story, "Index"),
                    }
                    if text_normalizations:
                        story_extra["text_normalizations"] = text_normalizations
                    candidate = _candidate(
                        content_type,
                        content_type,
                        story,
                        story_range,
                        text,
                        story_extra,
                    )
                    candidate.story_rank = story_rank
                    candidate.order_start = order_start
                    candidate.extra["section_range_start"] = int(section_range.Start)
                    candidate.extra["section_range_end"] = int(section_range.End)
                    candidate.extra["page_range_source"] = section_range
                    candidates.append(candidate)

    def _materialize(
        self,
        candidates: list[_Candidate],
        *,
        document: Any,
        doc_id: str,
        source_path: Path,
        source_format: str,
        created_at: str,
    ) -> list[ParsedContentUnitV1]:
        page_index = _build_page_index(document)
        for candidate in candidates:
            page_range = candidate.extra.pop("page_range_source", candidate.range_object)
            if page_index is None:
                candidate.page_no = _physical_page(document, page_range, at_end=False)
                candidate.page_end = _physical_page(document, page_range, at_end=True)
            else:
                start = int(page_range.Start)
                end = int(page_range.End)
                candidate.page_no = page_index.page_at(start)
                candidate.page_end = page_index.page_at(max(start, end - 1))
        candidates.sort(
            key=lambda item: (
                item.page_no,
                item.story_rank,
                item.order_start,
                _KIND_RANK.get(item.kind, 99),
                item.range_end,
            )
        )
        per_page_order: defaultdict[int, int] = defaultdict(int)
        units: list[ParsedContentUnitV1] = []
        for candidate in candidates:
            per_page_order[candidate.page_no] += 1
            unit_order = per_page_order[candidate.page_no]
            parse_unit_id = (
                f"{doc_id}_p{candidate.page_no:04d}_u{unit_order:04d}_{candidate.content_type}"
            )
            text = candidate.text
            image_uri: str | None = None
            table_uri: str | None = None
            initial_flags: list[str] = []
            extra = {
                "word_type": candidate.content_type.removesuffix("_block"),
                "unit_order_in_page": unit_order,
                "bbox": None,
                "language": language_of(text),
                "title_level": candidate.extra.pop("title_level", None),
                "style_name": candidate.extra.pop("style_name", None),
                "outline_level": candidate.extra.pop("outline_level", None),
                "list_level": candidate.extra.pop("list_level", None),
                "source_range_start": candidate.range_start,
                "source_range_end": candidate.range_end,
                "page_end": candidate.page_end,
                **candidate.extra,
            }
            if candidate.content_type == "table_block":
                table = self.tables.serialize(candidate.word_object, parse_unit_id)
                text = table.text
                table_uri = table.table_uri
                extra.update(
                    {
                        "row_count": table.row_count,
                        "column_count": table.column_count,
                        "merged_cells": table.merged_cells,
                    }
                )
                if table.text_normalizations:
                    extra["text_normalizations"] = table.text_normalizations
                if table.degraded:
                    initial_flags.append("table_structure_degraded")
            elif candidate.content_type in {"image_block", "chart_block"}:
                asset = self.assets.extract(
                    candidate.word_object,
                    parse_unit_id,
                    is_chart=candidate.content_type == "chart_block",
                )
                image_uri = asset.image_uri
                initial_flags.extend(asset.flags)
                extra.update(asset.metadata)
            elif candidate.content_type == "formula_block" and not text:
                initial_flags.append("formula_structure_degraded")

            score, flags = unit_quality(
                text,
                content_type=candidate.content_type,
                initial_flags=initial_flags,
                has_asset=bool(image_uri or table_uri),
            )
            unit = ParsedContentUnitV1(
                parse_unit_id=parse_unit_id,
                doc_id=doc_id,
                source_uri=str(source_path),
                source_name=source_path.name,
                source_format=source_format,
                page_no=candidate.page_no,
                block_id=parse_unit_id,
                content_type=candidate.content_type,
                text=text,
                image_uri=image_uri,
                table_uri=table_uri,
                quality_score=score,
                quality_flags=flags,
                created_at=created_at,
                extra=extra,
            )
            units.append(unit)
        return units


def _release_candidates(candidates: list[_Candidate]) -> None:
    """Release child COM proxies before the owning Document is closed."""

    for candidate in candidates:
        candidate.extra.pop("page_range_source", None)
        candidate.word_object = None
        candidate.range_object = None
    candidates.clear()
    gc.collect()


def _candidate(
    kind: str,
    content_type: str,
    word_object: Any,
    range_object: Any,
    text: str = "",
    extra: dict[str, Any] | None = None,
) -> _Candidate:
    start = int(range_object.Start)
    end = int(range_object.End)
    return _Candidate(
        kind=kind,
        content_type=content_type,
        word_object=word_object,
        range_object=range_object,
        range_start=start,
        range_end=end,
        order_start=start,
        text=text,
        extra=extra or {},
    )


def _iter_collection(collection: Any) -> Iterable[Any]:
    try:
        count = int(collection.Count)
    except Exception:
        return
    for index in range(1, count + 1):
        try:
            yield collection.Item(index)
        except AttributeError:
            yield collection(index)


def _iter_body_paragraphs(document: Any, table_ranges: list[tuple[int, int]]) -> Iterable[Any]:
    """Enumerate paragraph collections only in main-story gaps outside tables."""

    seen_starts: set[int] = set()
    try:
        content = document.Content
        content_start = int(content.Start)
        content_end = int(content.End)
        cursor = content_start
        gaps: list[tuple[int, int]] = []
        for table_start, table_end in table_ranges:
            if table_start > cursor:
                gaps.append((cursor, min(table_start, content_end)))
            cursor = max(cursor, table_end)
        if cursor < content_end:
            gaps.append((cursor, content_end))
        for gap_start, gap_end in gaps:
            if gap_start >= gap_end:
                continue
            gap_range = document.Range(Start=gap_start, End=gap_end)
            for paragraph in _iter_collection(gap_range.Paragraphs):
                start = int(paragraph.Range.Start)
                if start in seen_starts or any(table_start <= start < table_end for table_start, table_end in table_ranges):
                    continue
                seen_starts.add(start)
                yield paragraph
        return
    except Exception:
        # Fakes and some protected documents may not expose range-local
        # Paragraphs.  The filtered whole-document fallback is still correct.
        pass
    for paragraph in _iter_collection(document.Paragraphs):
        start = int(paragraph.Range.Start)
        if start in seen_starts or any(table_start <= start < table_end for table_start, table_end in table_ranges):
            continue
        seen_starts.add(start)
        yield paragraph


def _physical_page(document: Any, range_object: Any, *, at_end: bool) -> int:
    start = int(range_object.Start)
    end = int(range_object.End)
    position = max(start, end - 1) if at_end else start
    try:
        probe = document.Range(Start=position, End=position)
        page = int(probe.Information(WD_ACTIVE_END_PAGE_NUMBER))
    except Exception as exc:
        raise PageNumberUnavailableError(f"Word page number is unavailable at range {position}: {exc}") from exc
    if page < 1:
        raise PageNumberUnavailableError(f"Word returned invalid page number {page} at range {position}")
    return page


def _build_page_index(document: Any) -> _PageIndex | None:
    """Use Word-provided page ranges, never text length, to index physical pages."""

    try:
        page_count = int(document.ComputeStatistics(WD_STATISTIC_PAGES))
        if page_count < 1:
            raise ValueError(f"invalid Word page count: {page_count}")
        boundaries: list[tuple[int, int]] = []
        for page_ordinal in range(1, page_count + 1):
            page_range = document.GoTo(
                What=WD_GO_TO_PAGE,
                Which=WD_GO_TO_ABSOLUTE,
                Count=page_ordinal,
            )
            physical_page = int(page_range.Information(WD_ACTIVE_END_PAGE_NUMBER))
            if physical_page < 1:
                raise ValueError(f"invalid Word physical page number: {physical_page}")
            boundaries.append((int(page_range.Start), physical_page))
        boundaries.sort()
        starts = tuple(start for start, _ in boundaries)
        if len(set(starts)) != len(starts):
            raise ValueError("Word returned duplicate page boundary positions")
        return _PageIndex(starts, tuple(page for _, page in boundaries))
    except Exception:
        # Some protected/legacy documents do not expose GoTo reliably.  The
        # caller then falls back to Range.Information for every unit and still
        # fails explicitly if Word cannot supply a page number.
        return None


def _range_is_in_table(range_object: Any) -> bool:
    try:
        return bool(range_object.Information(WD_WITHIN_TABLE))
    except Exception:
        try:
            return int(range_object.Tables.Count) > 0
        except Exception:
            return False


def _paragraph_semantics(paragraph: Any, paragraph_range: Any, text: str) -> tuple[str, dict[str, Any], str]:
    style_name = _style_name(paragraph, paragraph_range)
    outline_level = _safe_int(paragraph, "OutlineLevel")
    if outline_level is None:
        outline_level = _safe_int(paragraph_range, "OutlineLevel")
    title_level: int | None = None
    if outline_level is not None and 1 <= outline_level < WD_OUTLINE_LEVEL_BODY_TEXT:
        title_level = outline_level
    else:
        match = re.match(r"^(?:Heading|标题)\s*([1-9])\b", style_name, flags=re.IGNORECASE)
        if match:
            title_level = int(match.group(1))

    list_format = getattr(paragraph_range, "ListFormat", None)
    list_type = _safe_int(list_format, "ListType")
    list_level = _safe_int(list_format, "ListLevelNumber")
    list_value = _safe_string(list_format, "ListString")
    is_list = list_type not in {None, WD_LIST_NO_NUMBERING}
    if title_level is not None:
        if list_value and not text.startswith(list_value):
            text = f"{list_value} {text}"
        return (
            "title",
            {
                "style_name": style_name or None,
                "outline_level": outline_level,
                "title_level": title_level,
                "list_level": list_level if is_list else None,
                "list_type": list_type if is_list else None,
                "list_value": list_value or None,
            },
            text,
        )
    if is_list:
        if list_value and not text.startswith(list_value):
            text = f"{list_value} {text}"
        return (
            "list",
            {
                "style_name": style_name or None,
                "outline_level": outline_level,
                "title_level": None,
                "list_level": list_level,
                "list_type": list_type,
                "list_value": list_value or None,
            },
            text,
        )
    return (
        "paragraph",
        {
            "style_name": style_name or None,
            "outline_level": outline_level,
            "title_level": None,
            "list_level": None,
        },
        text,
    )


def _style_name(paragraph: Any, paragraph_range: Any) -> str:
    for owner in (paragraph, paragraph_range):
        try:
            style = owner.Style
            if isinstance(style, str):
                value = style.strip()
            else:
                value = str(style.NameLocal).strip()
            if value:
                return value
        except Exception:
            pass
    return ""


def _has_chart(shape: Any) -> bool:
    try:
        return bool(shape.HasChart)
    except Exception:
        try:
            shape.Chart
            return True
        except Exception:
            return False


def _shape_text(shape: Any) -> tuple[str, list[str]]:
    for attribute in ("AlternativeText", "Title"):
        value = _safe_string(shape, attribute)
        if value:
            return normalize_word_text(value)
    try:
        if shape.TextFrame.HasText:
            return normalize_word_text(str(shape.TextFrame.TextRange.Text))
    except Exception:
        pass
    return "", []


def _clean_word_text(text: str) -> str:
    """Backward-compatible wrapper used by local tests and callers."""

    return normalize_word_text(text)[0]


def _safe_int(value: Any, attribute: str) -> int | None:
    try:
        return int(getattr(value, attribute))
    except Exception:
        return None


def _safe_string(value: Any, attribute: str) -> str:
    try:
        return str(getattr(value, attribute)).strip()
    except Exception:
        return ""


def _safe_scalar(value: Any, attribute: str) -> str | int | float | bool | None:
    try:
        result = getattr(value, attribute)
        if isinstance(result, (str, int, float, bool)):
            return result
    except Exception:
        pass
    return None
