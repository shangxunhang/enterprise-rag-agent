from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from document_ingestion.hashing import sha256_file
from document_ingestion.word_application import WordApplication
from document_ingestion.word_parser import WordParser


class Collection:
    def __init__(self, *items: object) -> None:
        self.items = list(items)
        self.Count = len(items)

    def Item(self, index: int) -> object:
        return self.items[index - 1]


class ListFormat:
    def __init__(self, list_type: int = 0, level: int = 0, value: str = "") -> None:
        self.ListType = list_type
        self.ListLevelNumber = level
        self.ListString = value


class Style:
    def __init__(self, name: str) -> None:
        self.NameLocal = name


class Range:
    def __init__(
        self,
        start: int,
        end: int,
        text: str = "",
        *,
        in_table: bool = False,
        list_format: ListFormat | None = None,
    ) -> None:
        self.Start = start
        self.End = end
        self.Text = text
        self.in_table = in_table
        self.ListFormat = list_format or ListFormat()
        self.Tables = Collection()

    def Information(self, kind: int) -> int | bool:
        if kind == 12:
            return self.in_table
        raise AssertionError(f"unexpected direct range information kind {kind}")


class Paragraph:
    def __init__(
        self,
        start: int,
        text: str,
        *,
        style: str = "正文",
        outline: int = 10,
        list_format: ListFormat | None = None,
    ) -> None:
        self.Range = Range(start, start + len(text) + 1, text + "\r", list_format=list_format)
        self.Style = Style(style)
        self.OutlineLevel = outline


class Cell:
    def __init__(self, row: int, column: int, text: str) -> None:
        self.RowIndex = row
        self.ColumnIndex = column
        self.Range = Range(0, 0, text + "\r\x07")


class TableRange(Range):
    def __init__(self, start: int, end: int) -> None:
        super().__init__(start, end)
        self.Cells = Collection(
            Cell(1, 1, "设备"),
            Cell(1, 2, "数量"),
            Cell(2, 1, "服务器"),
            Cell(2, 2, "4"),
        )


class Table:
    def __init__(self, start: int, end: int) -> None:
        self.Range = TableRange(start, end)
        self.Rows = Collection(object(), object())
        self.Columns = Collection(object(), object())
        self.NestingLevel = 1
        self.Title = "设备表"


class InlineShape:
    def __init__(self, start: int) -> None:
        self.Range = Range(start, start + 1)
        self.HasChart = False
        self.Type = 3
        self.AlternativeText = "架构图"
        self.Title = ""


class Formula:
    def __init__(self, start: int, text: str) -> None:
        self.Range = Range(start, start + len(text), text)


class Probe:
    def __init__(self, position: int, page: int | None = None) -> None:
        self.position = position
        self.Start = position
        self.End = position
        self.page = page

    def Information(self, kind: int) -> int:
        assert kind == 3
        return self.page or (1 if self.position < 100 else 2)


class FakeDocument:
    def __init__(self) -> None:
        self.Paragraphs = Collection(
            Paragraph(0, "项目标题", style="标题 1", outline=1),
            Paragraph(10, "段落 A"),
            Paragraph(30, "段落 B"),
            Paragraph(120, "段落 C"),
            Paragraph(130, "第一项", list_format=ListFormat(2, 1, "1.")),
        )
        self.Tables = Collection(Table(20, 29))
        self.InlineShapes = Collection(InlineShape(40))
        self.Shapes = Collection()
        self.OMaths = Collection(Formula(50, "x=1"))
        self.Sections = Collection()

    def Repaginate(self) -> None:
        pass

    def Range(self, *, Start: int, End: int) -> Probe:
        assert Start == End
        return Probe(Start)

    def ComputeStatistics(self, kind: int) -> int:
        assert kind == 2
        return 2

    def GoTo(self, *, What: int, Which: int, Count: int) -> Probe:
        assert What == 1
        assert Which == 1
        return Probe(0 if Count == 1 else 100, page=Count)


class FakeWord:
    def __init__(self, document: FakeDocument) -> None:
        self.document = document

    @contextmanager
    def open_document(self, path: Path):
        assert path.exists()
        yield self.document


def test_parser_preserves_range_order_pages_and_assets(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(b"fake docx")
    output = tmp_path / "out"
    parser = WordParser(output)
    units = parser.parse(
        source,
        source_path=source,
        source_format="docx",
        source_sha256=sha256_file(source),
        doc_id="doc_stable",
        created_at="2026-08-02T00:00:00+00:00",
        word=FakeWord(FakeDocument()),  # type: ignore[arg-type]
    )
    assert [unit.content_type for unit in units] == [
        "title",
        "paragraph",
        "table_block",
        "paragraph",
        "image_block",
        "formula_block",
        "paragraph",
        "list",
    ]
    assert [unit.page_no for unit in units] == [1, 1, 1, 1, 1, 1, 2, 2]
    assert [unit.extra["unit_order_in_page"] for unit in units] == [1, 2, 3, 4, 5, 6, 1, 2]
    table = units[2]
    assert table.table_uri == f"tables/{table.block_id}.json"
    table_payload = json.loads((output / table.table_uri).read_text(encoding="utf-8"))
    assert table_payload["rows"] == [["设备", "数量"], ["服务器", "4"]]
    assert table_payload["row_count"] == 2
    assert table_payload["column_count"] == 2
    image = units[4]
    assert image.image_uri is None
    assert "asset_not_extracted" in image.quality_flags
    assert units[-1].text == "1. 第一项"
    assert units[-1].extra["list_level"] == 1
    assert units[0].extra["title_level"] == 1


def test_parser_identity_is_stable_across_repeated_runs(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(b"fake docx")
    parser = WordParser(tmp_path / "out")

    def parse(created_at: str):
        return parser.parse(
            source,
            source_path=source,
            source_format="docx",
            source_sha256=sha256_file(source),
            doc_id="doc_stable",
            created_at=created_at,
            word=FakeWord(FakeDocument()),  # type: ignore[arg-type]
        )

    first = parse("2026-08-02T00:00:00+00:00")
    second = parse("2026-08-03T00:00:00+00:00")
    assert [unit.parse_unit_id for unit in first] == [unit.parse_unit_id for unit in second]
    assert [unit.block_id for unit in first] == [unit.block_id for unit in second]
    assert [unit.page_no for unit in first] == [unit.page_no for unit in second]


def test_numbered_heading_remains_title_not_list(tmp_path: Path) -> None:
    document = FakeDocument()
    document.Paragraphs = Collection(
        Paragraph(0, "编号标题", style="标题 2", outline=2, list_format=ListFormat(3, 2, "1.1")),
        Paragraph(20, "\u200b"),
    )
    document.Tables = Collection()
    document.InlineShapes = Collection()
    document.OMaths = Collection()
    source = tmp_path / "source.docx"
    source.write_bytes(b"fake docx")
    units = WordParser(tmp_path / "out").parse(
        source,
        source_path=source,
        source_format="docx",
        source_sha256=sha256_file(source),
        doc_id="doc_numbered_heading",
        created_at="2026-08-02T00:00:00+00:00",
        word=FakeWord(document),  # type: ignore[arg-type]
    )
    assert units[0].content_type == "title"
    assert units[0].text == "1.1 编号标题"
    assert units[0].extra["title_level"] == 2
    assert units[0].extra["list_level"] == 2
    assert len(units) == 1


def test_parser_normalizes_word_caption_separator_and_records_operation(tmp_path: Path) -> None:
    document = FakeDocument()
    document.Paragraphs = Collection(Paragraph(0, "表3\x1e1 业务应用系统现状表"))
    document.Tables = Collection()
    document.InlineShapes = Collection()
    document.Shapes = Collection()
    document.OMaths = Collection()
    source = tmp_path / "source.docx"
    source.write_bytes(b"fake docx")

    units = WordParser(tmp_path / "out").parse(
        source,
        source_path=source,
        source_format="docx",
        source_sha256=sha256_file(source),
        doc_id="doc_caption_separator",
        created_at="2026-08-02T00:00:00+00:00",
        word=FakeWord(document),  # type: ignore[arg-type]
    )

    assert units[0].text == "表3-1 业务应用系统现状表"
    assert "normalized_word_caption_separator" in units[0].extra["text_normalizations"]
    assert "control_characters" not in units[0].quality_flags


@pytest.mark.word_com
@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("RUN_WORD_COM_TESTS") != "1",
    reason="set RUN_WORD_COM_TESTS=1 on Windows with Microsoft Word installed",
)
def test_real_word_com_two_page_opt_in(tmp_path: Path) -> None:
    source = tmp_path / "two-pages.docx"
    with WordApplication() as word:
        document = word.application.Documents.Add()
        word.capture_document_window(document)
        try:
            document.Content.Text = "第一页正文"
            end = document.Content.End - 1
            document.Range(Start=end, End=end).InsertBreak(7)
            document.Range(Start=document.Content.End - 1, End=document.Content.End - 1).InsertAfter("第二页正文")
            document.SaveAs2(FileName=str(source), FileFormat=16, AddToRecentFiles=False)
        finally:
            document.Close(SaveChanges=0)
            del document
    assert word.process_exited is True
    with WordApplication() as word:
        units = WordParser(tmp_path / "out").parse(
            source,
            source_path=source,
            source_format="docx",
            source_sha256=sha256_file(source),
            doc_id="doc_real_two_pages",
            created_at="2026-08-02T00:00:00+00:00",
            word=word,
        )
    assert word.process_exited is True
    pages = {unit.page_no for unit in units if "正文" in unit.text}
    assert pages == {1, 2}
