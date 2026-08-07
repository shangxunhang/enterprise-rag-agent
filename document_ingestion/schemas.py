"""In-memory semantic model for the existing raw parsed-unit schema."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .contract import PARSER_NAME, PARSE_VERSION, SCHEMA_VERSION, validate_record


@dataclass(slots=True)
class ParsedContentUnitV1:
    """A Word unit serialized exactly like the current MinerU parsed unit."""

    parse_unit_id: str
    doc_id: str
    source_uri: str
    source_name: str
    source_format: str
    page_no: int
    block_id: str
    content_type: str
    text: str
    created_at: str
    extra: dict[str, Any]
    image_uri: str | None = None
    table_uri: str | None = None
    quality_score: float | None = None
    quality_flags: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    text_length: int = field(init=False)
    parser: str = PARSER_NAME
    parse_version: str = PARSE_VERSION
    ocr_used: bool = False

    def __post_init__(self) -> None:
        self.text_length = len(self.text)

    def to_dict(self) -> dict[str, Any]:
        """Serialize in the exact top-level field order used by the contract."""

        raw = asdict(self)
        ordered = {
            "schema_version": raw["schema_version"],
            "parse_unit_id": raw["parse_unit_id"],
            "doc_id": raw["doc_id"],
            "source_uri": raw["source_uri"],
            "source_name": raw["source_name"],
            "source_format": raw["source_format"],
            "page_no": raw["page_no"],
            "block_id": raw["block_id"],
            "content_type": raw["content_type"],
            "text": raw["text"],
            "text_length": raw["text_length"],
            "image_uri": raw["image_uri"],
            "table_uri": raw["table_uri"],
            "parser": raw["parser"],
            "parse_version": raw["parse_version"],
            "ocr_used": raw["ocr_used"],
            "quality_score": raw["quality_score"],
            "quality_flags": raw["quality_flags"],
            "created_at": raw["created_at"],
            "extra": raw["extra"],
        }
        validate_record(ordered, word_output=True)
        return ordered
