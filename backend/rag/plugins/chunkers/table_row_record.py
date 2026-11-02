"""Deterministic table-row record chunking for Stage C."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from rag.configs.SchemaConfig import (
    DEFAULT_CHILD_CHUNK_VERSION,
    DEFAULT_PARENT_CHUNK_VERSION,
)
from rag.ports.chunking import ParentChildChunkResult
from rag.plugins.chunkers.structured_common import (
    LogicalChildSpec,
    LogicalParentSpec,
    join_unit_text,
    materialize_document,
    normalize_units,
)


_SEPARATOR_CELL = re.compile(r"^:?-{2,}:?$")


@dataclass(frozen=True)
class ParsedTableRow:
    row_index: int
    header: list[str]
    cells: list[str]
    expanded_cells: list[str]
    normalized_fields: dict[str, str]
    inherited_cells: dict[str, str]


def _split_markdown_row(line: str) -> list[str]:
    value = str(line or "").strip()
    if not value:
        return []
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [cell.strip() for cell in value.split("|")]


def _is_separator(row: Sequence[str]) -> bool:
    non_empty = [cell for cell in row if cell]
    return bool(non_empty) and all(_SEPARATOR_CELL.match(cell) for cell in non_empty)


def _unique_headers(values: Sequence[str]) -> list[str]:
    output: list[str] = []
    counts: dict[str, int] = {}
    for index, value in enumerate(values, start=1):
        base = str(value or "").strip() or f"column_{index}"
        counts[base] = counts.get(base, 0) + 1
        output.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    return output


def parse_structured_table(
    payload: dict[str, Any],
) -> tuple[list[str], list[ParsedTableRow]]:
    """Parse reviewed row/cell JSON emitted by the document parser."""

    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        return [], []
    rows = [
        [str(cell or "").strip() for cell in row]
        for row in raw_rows
        if isinstance(row, list)
    ]
    if not rows:
        return [], []
    width = int(payload.get("column_count") or max(map(len, rows)))
    rows = [(row + [""] * (width - len(row)))[:width] for row in rows]
    header_depth = 1
    if len(rows) >= 2:
        first_non_empty = sum(bool(cell) for cell in rows[0])
        second_non_empty = sum(bool(cell) for cell in rows[1])
        if first_non_empty < width and second_non_empty >= first_non_empty:
            header_depth = 2
    header_rows = rows[:header_depth]
    data_rows = rows[header_depth:]
    combined = []
    for column in range(width):
        parts = [row[column] for row in header_rows if row[column]]
        combined.append(" / ".join(dict.fromkeys(parts)))
    header = _unique_headers(combined)
    merged_positions = {
        (int(item.get("row") or -1), int(item.get("column") or -1))
        for item in payload.get("merged_cells") or []
        if isinstance(item, dict)
    }
    previous_non_empty = [""] * width
    parsed: list[ParsedTableRow] = []
    for source_row_number, cells in enumerate(data_rows, start=header_depth + 1):
        if not any(cells):
            continue
        expanded = list(cells)
        inherited: dict[str, str] = {}
        for column, cell in enumerate(cells, start=1):
            if cell:
                previous_non_empty[column - 1] = cell
                continue
            if (
                (source_row_number, column) in merged_positions
                and previous_non_empty[column - 1]
            ):
                expanded[column - 1] = previous_non_empty[column - 1]
                inherited[header[column - 1]] = previous_non_empty[column - 1]
        normalized = {
            header[index]: expanded[index]
            for index in range(width)
            if expanded[index] != ""
        }
        if normalized:
            parsed.append(
                ParsedTableRow(
                    row_index=source_row_number,
                    header=list(header),
                    cells=list(cells),
                    expanded_cells=expanded,
                    normalized_fields=normalized,
                    inherited_cells=inherited,
                )
            )
    return header, parsed


def parse_markdown_table(text: str) -> tuple[list[str], list[ParsedTableRow]]:
    """Parse one markdown-like table without inventing missing values."""

    rows = [
        row
        for row in (
            _split_markdown_row(line)
            for line in str(text or "").splitlines()
            if "|" in line
        )
        if row
    ]
    if not rows:
        return [], []

    separator_index = next(
        (index for index, row in enumerate(rows) if _is_separator(row)),
        None,
    )
    if separator_index is not None:
        header_rows = rows[:separator_index]
        data_rows = rows[separator_index + 1 :]
    elif all(len(row) == 2 for row in rows):
        header_rows = [["field", "value"]]
        data_rows = rows
    elif len(rows) >= 2:
        header_rows = [rows[0]]
        data_rows = rows[1:]
    else:
        return [], []

    width = max(
        [len(row) for row in [*header_rows, *data_rows]],
        default=0,
    )
    if width == 0:
        return [], []
    padded_headers = [row + [""] * (width - len(row)) for row in header_rows]
    combined_headers: list[str] = []
    for column in range(width):
        parts = [
            row[column]
            for row in padded_headers
            if column < len(row) and row[column]
        ]
        combined_headers.append(" / ".join(dict.fromkeys(parts)))
    header = _unique_headers(combined_headers)

    previous_non_empty = [""] * width
    parsed: list[ParsedTableRow] = []
    for source_index, raw_row in enumerate(data_rows, start=1):
        cells = raw_row + [""] * (width - len(raw_row))
        cells = cells[:width]
        if not any(cells):
            continue
        expanded = list(cells)
        inherited: dict[str, str] = {}
        for column, cell in enumerate(cells):
            if cell:
                previous_non_empty[column] = cell
                continue
            if previous_non_empty[column]:
                expanded[column] = previous_non_empty[column]
                inherited[header[column]] = previous_non_empty[column]
        normalized = {
            header[column]: expanded[column]
            for column in range(width)
            if expanded[column] != ""
        }
        if not normalized:
            continue
        parsed.append(
            ParsedTableRow(
                row_index=source_index,
                header=list(header),
                cells=list(cells),
                expanded_cells=expanded,
                normalized_fields=normalized,
                inherited_cells=inherited,
            )
        )
    return header, parsed


def parse_explicit_calculation_record(
    units: Sequence[dict[str, Any]],
) -> tuple[list[str], list[str], dict[str, str]] | None:
    """Parse an explicit calculation narrative into one source-grounded row."""

    text = join_unit_text(units)
    patterns: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("device_count", re.compile(r"新建\s*(\d+)\s*台")),
        ("total_power_kw", re.compile(r"总(?:共)?功耗\s*(\d+(?:\.\d+)?)\s*KW", re.I)),
        (
            "emission_factor_kg_co2_per_kwh",
            re.compile(
                r"碳排放因子(?:为|=)\s*(\d+(?:\.\d+)?)\s*[（(]?kgCO[₂2]/kWh",
                re.I,
            ),
        ),
        (
            "annual_electricity_kwh",
            re.compile(r"=\s*(\d+(?:\.\d+)?)\s*kWh", re.I),
        ),
        (
            "annual_co2_tons",
            re.compile(r"(?:≈|为)\s*(\d+(?:\.\d+)?)\s*吨CO[₂2]", re.I),
        ),
    )
    normalized: dict[str, str] = {}
    for field_name, pattern in patterns:
        matches = list(pattern.finditer(text))
        if matches:
            normalized[field_name] = matches[-1].group(1)
    if len(normalized) < 3:
        return None
    headers = list(normalized)
    cells = [normalized[key] for key in headers]
    return headers, cells, normalized


def _table_title(
    units: Sequence[dict[str, Any]], table_index: int
) -> str | None:
    for unit in reversed(units[:table_index]):
        if unit.get("unit_type") != "table":
            value = str(unit.get("text") or "").strip()
            if value and len(value) <= 160:
                return value
    return None


class TableRowRecordChunker:
    """Create one indexed child per logical table row."""

    STRATEGY = "table_row_record"

    def __init__(
        self,
        *,
        parent_chunk_size: int = 4096,
        parent_chunk_overlap: int = 0,
        child_chunk_size: int = 4096,
        child_chunk_overlap: int = 0,
        unit: str = "char",
        parent_chunk_version: str = DEFAULT_PARENT_CHUNK_VERSION,
        child_chunk_version: str = DEFAULT_CHILD_CHUNK_VERSION,
        deterministic_created_at: str | None = None,
        chunker_name: str = "table_row_record",
        build_context: Any = None,
    ) -> None:
        self.parent_chunk_size = int(parent_chunk_size)
        self.parent_chunk_version = str(parent_chunk_version)
        self.child_chunk_version = str(child_chunk_version)
        self.created_at = deterministic_created_at
        self.chunker_name = chunker_name
        self._stats: dict[str, int] = {}

    def _chunk_doc(
        self, doc_id: str, records: list[dict[str, Any]]
    ) -> ParentChildChunkResult:
        units = normalize_units(records)
        logical_parents: list[LogicalParentSpec] = []
        table_count = 0
        row_count = 0
        fallback_chunks = 0
        empty_tables_filtered = 0

        for unit_index, unit in enumerate(units):
            if unit.get("unit_type") != "table":
                continue
            table_count += 1
            table_id = f"{doc_id}_table_{table_count:04d}"
            title = _table_title(units, unit_index)
            unit_extra = unit.get("extra")
            structured_table = (
                unit_extra.get("structured_table")
                if isinstance(unit_extra, dict)
                else None
            )
            if isinstance(structured_table, dict):
                header, rows = parse_structured_table(structured_table)
            else:
                header, rows = parse_markdown_table(
                    str(unit.get("text") or "")
                )
            if not rows and isinstance(structured_table, dict):
                empty_tables_filtered += 1
                continue
            if not rows:
                fallback_chunks += 1
                child = LogicalChildSpec(
                    text=str(unit.get("text") or ""),
                    units=[unit],
                    representation_type="text_evidence",
                    structure_path=[title or table_id],
                    metadata={
                        "table_id": table_id,
                        "table_title": title,
                        "header": header,
                        "source_page": unit.get("page_start"),
                    },
                    fallback_used=True,
                    fallback_reason="unparseable_table",
                    fallback_strategy="text_evidence",
                    actual_strategy="text_evidence",
                )
                logical_parents.append(
                    LogicalParentSpec(
                        children=[child],
                        units=[unit],
                        representation_type="table",
                        structure_path=[title or table_id],
                        title=title,
                        metadata={"table_id": table_id, "table_title": title},
                        fallback_used=True,
                        fallback_reason="unparseable_table",
                        fallback_strategy="text_evidence",
                        actual_strategy="text_evidence",
                    )
                )
                continue

            children: list[LogicalChildSpec] = []
            for row in rows:
                row_count += 1
                rendered = "\n".join(
                    f"{key}: {value}"
                    for key, value in row.normalized_fields.items()
                )
                if title:
                    rendered = f"{title}\n{rendered}"
                metadata = {
                    "table_id": table_id,
                    "table_title": title,
                    "header": row.header,
                    "row_index": row.row_index,
                    "cells": row.cells,
                    "expanded_cells": row.expanded_cells,
                    "normalized_fields": row.normalized_fields,
                    "inherited_cells": row.inherited_cells,
                    "source_page": unit.get("page_start"),
                    "source_unit_ids": [unit.get("unit_id")],
                    "structured_table_asset": (
                        unit_extra.get("structured_table_asset")
                        if isinstance(unit_extra, dict)
                        else None
                    ),
                }
                children.append(
                    LogicalChildSpec(
                        text=rendered,
                        units=[unit],
                        representation_type="table_row",
                        structure_path=[
                            title or table_id,
                            f"row_{row.row_index}",
                        ],
                        metadata=metadata,
                        actual_strategy=self.STRATEGY,
                    )
                )
            logical_parents.append(
                LogicalParentSpec(
                    children=children,
                    units=[unit],
                    representation_type="table",
                    structure_path=[title or table_id],
                    title=title,
                    metadata={
                        "table_id": table_id,
                        "table_title": title,
                        "header": header,
                        "row_count": len(rows),
                    },
                    actual_strategy=self.STRATEGY,
                )
            )

        if not logical_parents:
            calculation = parse_explicit_calculation_record(units)
            if calculation is not None:
                header, cells, normalized_fields = calculation
                title = str(units[0].get("text") or "").strip() or None
                table_id = f"{doc_id}_calculation_0001"
                child = LogicalChildSpec(
                    text="\n".join(
                        f"{key}: {value}"
                        for key, value in normalized_fields.items()
                    ),
                    units=units,
                    representation_type="table_row",
                    structure_path=[title or table_id, "row_1"],
                    metadata={
                        "table_id": table_id,
                        "table_title": title,
                        "header": header,
                        "row_index": 1,
                        "cells": cells,
                        "expanded_cells": cells,
                        "normalized_fields": normalized_fields,
                        "inherited_cells": {},
                        "source_page": units[0].get("page_start"),
                        "source_unit_ids": [u.get("unit_id") for u in units],
                        "calculation_record": True,
                    },
                    actual_strategy=self.STRATEGY,
                )
                logical_parents = [
                    LogicalParentSpec(
                        children=[child],
                        units=units,
                        representation_type="table",
                        structure_path=[title or table_id],
                        title=title,
                        metadata={
                            "table_id": table_id,
                            "table_title": title,
                            "header": header,
                            "row_count": 1,
                            "calculation_record": True,
                        },
                        actual_strategy=self.STRATEGY,
                    )
                ]
                row_count += 1
            else:
                fallback_chunks = 1
                text = join_unit_text(units)
                child = LogicalChildSpec(
                text=text,
                units=units,
                representation_type="text_evidence",
                structure_path=[],
                metadata={"source_unit_ids": [u.get("unit_id") for u in units]},
                fallback_used=True,
                fallback_reason="no_table_units",
                fallback_strategy="text_evidence",
                actual_strategy="text_evidence",
            )
                logical_parents = [
                    LogicalParentSpec(
                        children=[child],
                        units=units,
                        representation_type="document",
                        fallback_used=True,
                        fallback_reason="no_table_units",
                        fallback_strategy="text_evidence",
                        actual_strategy="text_evidence",
                    )
                ]

        result = materialize_document(
            doc_id=doc_id,
            parents=logical_parents,
            original_strategy=self.STRATEGY,
            parent_chunk_version=self.parent_chunk_version,
            child_chunk_version=self.child_chunk_version,
            created_at=self.created_at,
            chunker_name=self.chunker_name,
            max_parent_chars=self.parent_chunk_size,
        )
        self._stats = {
            "table_count": table_count,
            "table_row_count": row_count,
            "fallback_chunk_count": fallback_chunks,
            "empty_table_count_filtered": empty_tables_filtered,
        }
        return result

    def chunk_records(
        self, records: Iterable[dict[str, Any]]
    ) -> ParentChildChunkResult:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            doc_id = str(record.get("doc_id") or "").strip()
            if not doc_id:
                raise ValueError("table-row chunking requires doc_id")
            groups[doc_id].append(dict(record))
        parents: list[dict[str, Any]] = []
        children: list[dict[str, Any]] = []
        for doc_id in sorted(groups):
            result = self._chunk_doc(doc_id, groups[doc_id])
            parents.extend(result.parents)
            children.extend(result.children)
        return ParentChildChunkResult(parents=parents, children=children)

    def execution_metadata(self) -> dict[str, Any]:
        return {"strategy": self.STRATEGY, **self._stats}


__all__ = [
    "ParsedTableRow",
    "TableRowRecordChunker",
    "parse_explicit_calculation_record",
    "parse_markdown_table",
    "parse_structured_table",
]
