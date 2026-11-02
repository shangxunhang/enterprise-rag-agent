"""Pure association of owner-resolved Section Evidence and canonical Citations.

The caller owns Package/Bundle authorization and currentness.  Association
rows preserve source multiplicity; a Writer usage view may select only rows
that are uniquely resolvable and source-identity consistent.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable

from apps.enterprise_document.services.scheme_writer.constants import CITATION_PATTERN
from core.hashing import canonical_sha256
from schemas.citation import citation_source_identity


class CitationProjectionIntegrityError(ValueError):
    """A frozen citation/Artifact binding cannot be resolved exactly."""


@dataclass(frozen=True)
class CitationAssociation:
    alias: str
    citation_ref: str
    exact_evidence_ref: str
    evidence_id: str
    citation_ref_present: bool
    alias_well_formed: bool
    source_identity_consistent: bool
    citation_source_identity: str
    source_locator_ref: str
    source_locator: dict[str, Any]


_LOCATOR_FIELDS = (
    "source_type", "doc_id", "source_document_id", "file_id", "chunk_id",
    "child_chunk_id", "parent_chunk_id", "table_id", "row_ids",
    "page_start", "page_end",
)


def associate_selected_citations(*, package, bundle) -> tuple[CitationAssociation, ...]:
    """Return every selected Citation/Evidence pair, without I/O or deduplication.

    Inputs must already be resolved and authorized by their existing owners.
    The Package/Bundle selection intersection and output ordering match the
    original Section catalog projection exactly.
    """
    selected = set(bundle.selected_evidence_ids) & {
        selection.evidence_id for selection in package.selections
    }
    bundle_binding = package.evidence_bundle_binding.model_dump(mode="json")
    rows = []
    for citation in bundle.citations:
        items = [
            item for item in bundle.items
            if item.evidence_id in selected and citation.citation_id in item.citation_ids
        ]
        if not items:
            continue
        citation_ref_present = bool(citation.citation_ref)
        alias_well_formed = bool(CITATION_PATTERN.fullmatch("[" + citation.citation_id + "]"))
        source_identity = citation_source_identity(citation)
        locator = {name: deepcopy(getattr(citation, name)) for name in _LOCATOR_FIELDS}
        locator_ref = "citation-locator://sha256/" + canonical_sha256(locator)
        for item in sorted(items, key=lambda value: value.evidence_id):
            evidence_ref = "evidence-item://sha256/" + canonical_sha256({
                "bundle_binding": bundle_binding, "evidence_id": item.evidence_id,
            })
            rows.append(CitationAssociation(
                alias=citation.citation_id,
                citation_ref=citation.citation_ref,
                exact_evidence_ref=evidence_ref,
                evidence_id=item.evidence_id,
                citation_ref_present=citation_ref_present,
                alias_well_formed=alias_well_formed,
                source_identity_consistent=citation.citation_ref == source_identity,
                citation_source_identity=source_identity,
                source_locator_ref=locator_ref,
                source_locator=deepcopy(locator),
            ))
    return tuple(rows)


def writer_allowed_aliases(associations: Iterable[CitationAssociation]) -> tuple[str, ...]:
    """Project only uniquely resolvable, source-consistent aliases for Writer."""
    groups = defaultdict(list)
    for row in associations:
        groups[row.alias].append(row)
    return tuple(sorted(
        alias for alias, group in groups.items()
        if len(group) == 1 and group[0].citation_ref_present
        and group[0].alias_well_formed and group[0].source_identity_consistent
    ))
