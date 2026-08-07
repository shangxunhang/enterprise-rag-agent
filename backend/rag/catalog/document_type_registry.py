"""Default capabilities and parsing policies for Stage B document types."""

from __future__ import annotations

from dataclasses import dataclass

from .document_profile import ChunkStrategy, ClaimType, DocumentType, EvidenceRole, ParserStrategy


CURRENT_PROJECT_CLAIMS = (
    ClaimType.CURRENT_PROJECT_FACT,
    ClaimType.CURRENT_PROJECT_COST,
    ClaimType.CURRENT_PROJECT_QUANTITY,
    ClaimType.CURRENT_PROJECT_SCHEDULE,
    ClaimType.CURRENT_PROJECT_CONFIGURATION,
)

NON_TEMPLATE_CLAIMS = tuple(
    claim
    for claim in ClaimType
    if claim not in {ClaimType.TEMPLATE_STRUCTURE, ClaimType.STYLE_GUIDANCE}
)


@dataclass(frozen=True)
class DocumentTypePolicy:
    evidence_role: EvidenceRole
    kb_id: str
    allowed_claim_types: tuple[ClaimType, ...]
    forbidden_claim_types: tuple[ClaimType, ...]
    parser_strategy: ParserStrategy
    chunk_strategy: ChunkStrategy


DOCUMENT_TYPE_POLICIES: dict[DocumentType, DocumentTypePolicy] = {
    DocumentType.CURRENT_PROJECT_REQUIREMENT: DocumentTypePolicy(
        EvidenceRole.PROJECT_FACT,
        "project_fact_kb",
        CURRENT_PROJECT_CLAIMS,
        (),
        ParserStrategy.PROJECT_FACT_EXTRACTION,
        ChunkStrategy.FACT_RECORD,
    ),
    DocumentType.CURRENT_PROJECT_DECISION: DocumentTypePolicy(
        EvidenceRole.PROJECT_FACT,
        "project_fact_kb",
        CURRENT_PROJECT_CLAIMS,
        (),
        ParserStrategy.PROJECT_FACT_EXTRACTION,
        ChunkStrategy.FACT_RECORD,
    ),
    DocumentType.CURRENT_PROJECT_INVENTORY: DocumentTypePolicy(
        EvidenceRole.PROJECT_FACT,
        "project_fact_kb",
        (
            ClaimType.CURRENT_PROJECT_FACT,
            ClaimType.CURRENT_PROJECT_QUANTITY,
            ClaimType.CURRENT_PROJECT_CONFIGURATION,
        ),
        (ClaimType.CURRENT_PROJECT_COST, ClaimType.CURRENT_PROJECT_SCHEDULE),
        ParserStrategy.STRUCTURED_TABLE,
        ChunkStrategy.TABLE_ROW_RECORD,
    ),
    DocumentType.HISTORICAL_FEASIBILITY_STUDY: DocumentTypePolicy(
        EvidenceRole.HISTORICAL_CASE,
        "historical_case_kb",
        (ClaimType.GENERAL_METHOD, ClaimType.TYPICAL_ARCHITECTURE, ClaimType.REFERENCE_CASE),
        CURRENT_PROJECT_CLAIMS,
        ParserStrategy.HIERARCHICAL_DOCUMENT,
        ChunkStrategy.HEADING_PARENT_PARAGRAPH_CHILD,
    ),
    DocumentType.HISTORICAL_CONSTRUCTION_SCHEME: DocumentTypePolicy(
        EvidenceRole.HISTORICAL_CASE,
        "historical_case_kb",
        (ClaimType.GENERAL_METHOD, ClaimType.TYPICAL_ARCHITECTURE, ClaimType.REFERENCE_CASE),
        CURRENT_PROJECT_CLAIMS,
        ParserStrategy.HIERARCHICAL_DOCUMENT,
        ChunkStrategy.HEADING_PARENT_PARAGRAPH_CHILD,
    ),
    DocumentType.POLICY_STANDARD: DocumentTypePolicy(
        EvidenceRole.POLICY_STANDARD,
        "policy_standard_kb",
        (ClaimType.POLICY_REQUIREMENT,),
        CURRENT_PROJECT_CLAIMS,
        ParserStrategy.POLICY_CLAUSE,
        ChunkStrategy.POLICY_ARTICLE_CLAUSE,
    ),
    DocumentType.TECHNICAL_WHITEPAPER: DocumentTypePolicy(
        EvidenceRole.TECHNICAL_REFERENCE,
        "technical_reference_kb",
        (ClaimType.GENERAL_METHOD, ClaimType.TYPICAL_ARCHITECTURE, ClaimType.TECHNICAL_SPECIFICATION),
        CURRENT_PROJECT_CLAIMS,
        ParserStrategy.HIERARCHICAL_DOCUMENT,
        ChunkStrategy.HEADING_PARENT_PARAGRAPH_CHILD,
    ),
    DocumentType.TENDER_REQUIREMENT: DocumentTypePolicy(
        EvidenceRole.PROCUREMENT_REQUIREMENT,
        "project_fact_kb",
        (ClaimType.PROCUREMENT_REQUIREMENT, *CURRENT_PROJECT_CLAIMS),
        (),
        ParserStrategy.PROJECT_FACT_EXTRACTION,
        ChunkStrategy.FACT_RECORD,
    ),
    DocumentType.DOCUMENT_TEMPLATE: DocumentTypePolicy(
        EvidenceRole.TEMPLATE_STYLE,
        "template_style_kb",
        (ClaimType.TEMPLATE_STRUCTURE, ClaimType.STYLE_GUIDANCE),
        NON_TEMPLATE_CLAIMS,
        ParserStrategy.TEMPLATE_DOCUMENT,
        ChunkStrategy.TEMPLATE_SECTION,
    ),
    DocumentType.UNKNOWN: DocumentTypePolicy(
        EvidenceRole.UNCLASSIFIED,
        "unclassified_archive",
        (),
        tuple(ClaimType),
        ParserStrategy.GENERIC_TEXT,
        ChunkStrategy.FIXED_TOKEN_FALLBACK,
    ),
}


def get_document_type_policy(document_type: DocumentType) -> DocumentTypePolicy:
    return DOCUMENT_TYPE_POLICIES[document_type]


__all__ = ["CURRENT_PROJECT_CLAIMS", "DOCUMENT_TYPE_POLICIES", "DocumentTypePolicy", "get_document_type_policy"]
