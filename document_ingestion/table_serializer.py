"""Word table extraction and structured JSON asset serialization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .atomic_writer import atomic_write_json
from .safe_paths import safe_child
from .text_normalizer import normalize_word_text


@dataclass(frozen=True, slots=True)
class TableResult:
    text: str
    table_uri: str
    degraded: bool
    row_count: int
    column_count: int
    merged_cells: list[dict[str, Any]]
    text_normalizations: list[str]


class TableSerializer:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root.resolve()
        self.tables_root = self.output_root / "tables"

    def serialize(self, table: Any, block_id: str) -> TableResult:
        cells: dict[tuple[int, int], str] = {}
        degraded = False
        merged_cells: list[dict[str, Any]] = []
        text_normalizations: list[str] = []
        try:
            for fallback_index, cell in enumerate(_iter_collection(table.Range.Cells), 1):
                try:
                    row = int(cell.RowIndex)
                    column = int(cell.ColumnIndex)
                except Exception:
                    row = fallback_index
                    column = 1
                    degraded = True
                position = (row, column)
                value, cell_normalizations = normalize_word_text(str(cell.Range.Text))
                text_normalizations.extend(cell_normalizations)
                if position in cells:
                    degraded = True
                    merged_cells.append({"row": row, "column": column, "status": "overlapping_cell"})
                else:
                    cells[position] = value
        except Exception:
            degraded = True

        declared_rows = _safe_count(getattr(table, "Rows", None))
        declared_columns = _safe_count(getattr(table, "Columns", None))
        row_count = max([declared_rows, *(position[0] for position in cells)], default=0)
        column_count = max([declared_columns, *(position[1] for position in cells)], default=0)
        rows = [[cells.get((row, column), "") for column in range(1, column_count + 1)] for row in range(1, row_count + 1)]
        missing_positions = [
            {"row": row, "column": column, "status": "merged_or_unavailable"}
            for row in range(1, row_count + 1)
            for column in range(1, column_count + 1)
            if (row, column) not in cells
        ]
        if missing_positions:
            degraded = True
            merged_cells.extend(missing_positions)

        caption = _table_caption(table)
        payload = {
            "rows": rows,
            "row_count": row_count,
            "column_count": column_count,
            "merged_cells": merged_cells,
            "caption": caption,
        }
        asset_name = f"{block_id}.json"
        asset_path = safe_child(self.tables_root, asset_name)
        atomic_write_json(asset_path, payload, root=self.output_root)
        table_uri = f"tables/{asset_name}"
        return TableResult(
            text=_markdown_table(rows),
            table_uri=table_uri,
            degraded=degraded,
            row_count=row_count,
            column_count=column_count,
            merged_cells=merged_cells,
            text_normalizations=list(dict.fromkeys(text_normalizations)),
        )


def _iter_collection(collection: Any) -> Iterable[Any]:
    count = int(collection.Count)
    for index in range(1, count + 1):
        try:
            yield collection.Item(index)
        except AttributeError:
            yield collection(index)


def _safe_count(collection: Any) -> int:
    try:
        return int(collection.Count)
    except Exception:
        return 0


def _table_caption(table: Any) -> str | None:
    try:
        title = str(table.Title).strip()
        return title or None
    except Exception:
        return None


def _markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)

    def normalized(row: list[str]) -> list[str]:
        return [value.replace("|", "\\|").replace("\r", " ").replace("\n", " ") for value in row] + [
            ""
        ] * (width - len(row))

    header = normalized(rows[0])
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in range(width)) + " |"]
    lines.extend("| " + " | ".join(normalized(row)) + " |" for row in rows[1:])
    return "\n".join(lines)
