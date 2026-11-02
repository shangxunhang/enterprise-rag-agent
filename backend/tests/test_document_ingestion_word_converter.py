from __future__ import annotations

import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest

import document_ingestion.word_application as word_application_module
from document_ingestion.exceptions import ConversionCollisionError
from document_ingestion.hashing import sha256_file
from document_ingestion.word_application import WordApplication
from document_ingestion.word_converter import WordConverter, validate_docx


class FakeConvertedDocument:
    def __init__(self) -> None:
        self.save_kwargs: dict[str, object] | None = None

    def SaveAs2(self, **kwargs: object) -> None:
        self.save_kwargs = kwargs
        target = Path(str(kwargs["FileName"]))
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<w:document/>")


class FakeWord:
    def __init__(self, document: FakeConvertedDocument) -> None:
        self.document = document
        self.opened: Path | None = None

    @contextmanager
    def open_document(self, path: Path):
        self.opened = path
        yield self.document


def test_doc_conversion_is_controlled_and_reusable(tmp_path: Path) -> None:
    source = tmp_path / "legacy.doc"
    source.write_bytes(b"legacy source")
    output = tmp_path / "out"
    document = FakeConvertedDocument()
    word = FakeWord(document)
    converter = WordConverter(output)
    digest = sha256_file(source)
    first = converter.convert(source, doc_id="doc_stable", source_sha256=digest, word=word)  # type: ignore[arg-type]
    assert first.reused is False
    assert first.normalized_path != source
    assert source.read_bytes() == b"legacy source"
    assert document.save_kwargs is not None
    assert document.save_kwargs["FileFormat"] == 16
    assert document.save_kwargs["AddToRecentFiles"] is False
    assert document.save_kwargs["CompatibilityMode"] == 15
    temporary_target = Path(str(document.save_kwargs["FileName"]))
    assert temporary_target.parent == output / "normalized"
    assert temporary_target.name.startswith("doc_stable.")
    assert temporary_target.name.endswith(".pending.docx")
    assert not temporary_target.exists()
    validate_docx(first.normalized_path)
    second = converter.convert(source, doc_id="doc_stable", source_sha256=digest, word=word)  # type: ignore[arg-type]
    assert second.reused is True


def test_normalized_collision_does_not_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "legacy.doc"
    source.write_bytes(b"source one")
    converter = WordConverter(tmp_path / "out")
    converter.convert(
        source,
        doc_id="doc_collision",
        source_sha256=sha256_file(source),
        word=FakeWord(FakeConvertedDocument()),  # type: ignore[arg-type]
    )
    source.write_bytes(b"source two")
    with pytest.raises(ConversionCollisionError):
        converter.convert(
            source,
            doc_id="doc_collision",
            source_sha256=sha256_file(source),
            word=FakeWord(FakeConvertedDocument()),  # type: ignore[arg-type]
        )


def test_word_application_open_and_cleanup_on_exception(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(b"placeholder")

    class Document:
        closed = False

        def Close(self, **kwargs: object) -> None:
            assert kwargs == {"SaveChanges": 0}
            self.closed = True

    class Documents:
        def __init__(self, document: Document) -> None:
            self.document = document
            self.kwargs: dict[str, object] | None = None

        def Open(self, **kwargs: object) -> Document:
            self.kwargs = kwargs
            return self.document

    class App:
        def __init__(self) -> None:
            self.document = Document()
            self.Documents = Documents(self.document)
            self.Options = type("Options", (), {})()
            self.quit = False

        def Quit(self, *args: object, **kwargs: object) -> None:
            assert args == (0,)
            assert kwargs == {}
            self.quit = True

    app = App()
    with pytest.raises(RuntimeError, match="boom"):
        with WordApplication(dispatch_factory=lambda _: app) as word:
            with word.open_document(source):
                raise RuntimeError("boom")
    assert app.document.closed is True
    assert app.quit is True
    assert app.Visible is False
    assert app.DisplayAlerts == 0
    assert app.Documents.kwargs == {
        "FileName": str(source.resolve()),
        "ConfirmConversions": False,
        "ReadOnly": True,
        "AddToRecentFiles": False,
        "Revert": False,
        "NoEncodingDialog": True,
    }


def test_word_application_closes_only_its_owned_window(monkeypatch: pytest.MonkeyPatch) -> None:
    class App:
        Hwnd = 12345

        def __init__(self) -> None:
            self.Options = type("Options", (), {})()
            self.quit_called = False

        def Quit(self, *args: object, **kwargs: object) -> None:
            self.quit_called = True

    app = App()
    posted: list[int] = []
    monkeypatch.setattr(word_application_module, "_pid_from_hwnd", lambda hwnd: 67890)
    monkeypatch.setattr(
        word_application_module,
        "_post_window_close",
        lambda hwnd: posted.append(hwnd) or True,
    )
    monkeypatch.setattr(word_application_module, "_wait_for_process_exit", lambda pid: pid == 67890)

    with WordApplication(dispatch_factory=lambda _: app) as word:
        assert word.owned_window_handle == 12345
        assert word.owned_process_id == 67890

    assert posted == [12345]
    assert app.quit_called is False
    assert word.process_exited is True
