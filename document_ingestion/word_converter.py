"""Safe legacy DOC to controlled DOCX normalization."""

from __future__ import annotations

import json
import os
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .atomic_writer import atomic_write_json
from .exceptions import ConversionCollisionError, ConversionError
from .hashing import sha256_file
from .safe_paths import assert_within, safe_child, validate_safe_component
from .word_application import WordApplication

WD_FORMAT_DOCUMENT_DEFAULT = 16
WD_WORD_2013 = 15


@dataclass(frozen=True, slots=True)
class ConversionResult:
    normalized_path: Path
    reused: bool


class WordConverter:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root.resolve()
        self.normalized_root = self.output_root / "normalized"

    def convert(
        self,
        source_path: Path,
        *,
        doc_id: str,
        source_sha256: str,
        word: WordApplication,
    ) -> ConversionResult:
        validate_safe_component(doc_id, label="doc_id")
        source_path = source_path.resolve(strict=True)
        if source_path.suffix.lower() != ".doc":
            raise ConversionError("WordConverter only accepts legacy .doc sources")
        normalized = safe_child(self.normalized_root, f"{doc_id}.docx")
        metadata_path = safe_child(self.normalized_root, f"{doc_id}.normalization.json")
        if normalized.exists() or metadata_path.exists():
            if not normalized.is_file() or not metadata_path.is_file():
                raise ConversionCollisionError(f"incomplete normalized artifact for {doc_id}")
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ConversionCollisionError(f"invalid normalization metadata for {doc_id}") from exc
            if metadata.get("source_sha256") != source_sha256:
                raise ConversionCollisionError(f"normalized doc_id {doc_id} belongs to a different source")
            validate_docx(normalized)
            return ConversionResult(normalized, reused=True)

        self.normalized_root.mkdir(parents=True, exist_ok=True)
        temporary = safe_child(self.normalized_root, f"{doc_id}.{uuid.uuid4().hex}.pending.docx")
        try:
            with word.open_document(source_path) as document:
                document.SaveAs2(
                    FileName=str(temporary),
                    FileFormat=WD_FORMAT_DOCUMENT_DEFAULT,
                    AddToRecentFiles=False,
                    CompatibilityMode=WD_WORD_2013,
                )
            validate_docx(temporary)
            with temporary.open("r+b") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            assert_within(self.output_root, temporary)
            assert_within(self.output_root, normalized)
            os.replace(temporary, normalized)
            atomic_write_json(
                metadata_path,
                {
                    "doc_id": doc_id,
                    "source_name": source_path.name,
                    "source_sha256": source_sha256,
                    "normalized_sha256": sha256_file(normalized),
                },
                root=self.output_root,
            )
            return ConversionResult(normalized, reused=False)
        except ConversionError:
            raise
        except Exception as exc:
            raise ConversionError(f"DOC to DOCX conversion failed for {source_path.name}: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)


def validate_docx(path: Path) -> None:
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            raise ConversionError(f"normalized DOCX is empty or missing: {path}")
        with zipfile.ZipFile(path, "r") as archive:
            names = set(archive.namelist())
            required = {"[Content_Types].xml", "word/document.xml"}
            if not required.issubset(names) or archive.testzip() is not None:
                raise ConversionError(f"normalized DOCX is not a valid Word ZIP container: {path}")
    except zipfile.BadZipFile as exc:
        raise ConversionError(f"normalized DOCX is corrupt: {path}") from exc
