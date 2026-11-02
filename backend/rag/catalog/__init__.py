"""Document catalog contracts for evidence-aware RAG."""

from .document_classifier import DocumentProfileSeedSchema, classify_manifest_record
from .document_profile import (
    AuthorityLevel,
    AuthorityProfileSchema,
    ChunkStrategy,
    ClaimType,
    ClassificationMethod,
    ClassificationProfileSchema,
    ContentCapabilitiesSchema,
    DocumentProfileSchema,
    DocumentType,
    EvidenceRole,
    ParserStrategy,
    ParsingProfileSchema,
    ProjectRelation,
    ProjectRelationSchema,
)
from .document_profile_projection import (
    canonical_profile_hash,
    profile_is_gold,
    profile_should_index,
    project_source_record,
    source_record_sha256,
)
from .document_profile_registry import (
    DocumentProfileCatalogAuditSchema,
    DocumentProfileRegistry,
)
from .document_type_registry import (
    DOCUMENT_TYPE_POLICIES,
    DocumentTypePolicy,
    get_document_type_policy,
)

__all__ = [
    "AuthorityLevel",
    "AuthorityProfileSchema",
    "ChunkStrategy",
    "ClaimType",
    "ClassificationMethod",
    "ClassificationProfileSchema",
    "ContentCapabilitiesSchema",
    "DOCUMENT_TYPE_POLICIES",
    "DocumentProfileCatalogAuditSchema",
    "DocumentProfileRegistry",
    "DocumentProfileSchema",
    "DocumentProfileSeedSchema",
    "DocumentType",
    "DocumentTypePolicy",
    "EvidenceRole",
    "ParserStrategy",
    "ParsingProfileSchema",
    "ProjectRelation",
    "ProjectRelationSchema",
    "canonical_profile_hash",
    "classify_manifest_record",
    "get_document_type_policy",
    "profile_is_gold",
    "profile_should_index",
    "project_source_record",
    "source_record_sha256",
]
