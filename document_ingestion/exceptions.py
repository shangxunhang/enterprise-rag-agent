"""Domain exceptions for document ingestion."""


class DocumentIngestionError(Exception):
    """Base class for expected ingestion failures."""


class ContractValidationError(DocumentIngestionError):
    """A parsed unit does not match the shared raw parser contract."""


class UnsafePathError(DocumentIngestionError):
    """A path or filename failed containment or Windows safety checks."""


class WordUnavailableError(DocumentIngestionError):
    """Microsoft Word COM automation cannot be started."""


class ConversionError(DocumentIngestionError):
    """A legacy DOC file could not be converted safely."""


class ConversionCollisionError(ConversionError):
    """An existing normalized file belongs to another source."""


class ParseError(DocumentIngestionError):
    """Word content could not be parsed."""


class PageNumberUnavailableError(ParseError):
    """Word did not provide a reliable physical page number."""


class SourceIdentityError(DocumentIngestionError):
    """Persistent doc_id to source identity validation failed."""


class DocIdCollisionError(SourceIdentityError):
    """A doc_id is already bound to a different source digest."""


class OutputIdentityMissingError(SourceIdentityError):
    """Formal output exists without its required persistent source identity."""


class OutputArtifactConflictError(SourceIdentityError):
    """Existing formal output is corrupt or incompatible with its identity."""


class QualityRejectedError(ParseError):
    """Deterministic document-level quality gates rejected parsed output."""

    def __init__(self, flags: list[str]) -> None:
        self.flags = list(dict.fromkeys(flags))
        super().__init__(f"deterministic document quality rejection: {self.flags}")
