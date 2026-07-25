"""Occurrence-level citation marker and binding integrity."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Iterable, Sequence

from schemas.citation import CitationBindingSchema


_CITATION_PATTERN = re.compile(r"\[([A-Za-z0-9_.:\-]+)\]")
_PARAGRAPH_SEPARATOR = re.compile(r"\n\s*\n")


def _claim_for_marker(paragraph: str, marker_position: int) -> str:
    preceding = paragraph[:marker_position].rstrip()
    if not preceding:
        return paragraph.strip()
    non_empty_lines = [
        line.strip() for line in preceding.splitlines() if line.strip()
    ]
    candidate_line = non_empty_lines[-1] if non_empty_lines else preceding
    sentence_parts = [
        item.strip()
        for item in re.split(r"(?<=[。！？!?；;])", candidate_line)
        if item.strip()
    ]
    return sentence_parts[-1] if sentence_parts else candidate_line.strip()


def _normalized_claim(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = _CITATION_PATTERN.sub("", text)
    text = re.sub(r"^[\-+*\d.、（）()\s]+", "", text)
    return re.sub(r"[\s。！？!?；;，,]+", "", text).lower()


@dataclass(frozen=True)
class CitationMarkerOccurrence:
    target_section_id: str
    target_paragraph_id: str
    target_claim_id: str
    marker_index: int
    marker_ordinal: int
    marker_start: int
    marker_end: int
    citation_id: str
    claim_text: str

    @property
    def identity(self) -> str:
        return (
            f"{self.target_section_id}:marker:{self.marker_index:03d}:"
            f"{self.citation_id}"
        )


@dataclass(frozen=True)
class CitationOccurrenceIntegrityResult:
    checks: dict[str, bool]
    metadata: dict[str, Any]

    @property
    def passed(self) -> bool:
        return all(self.checks.values())


class CitationOccurrenceIntegrityGate:
    """Enforce one real marker occurrence to one verified binding."""

    @staticmethod
    def extract_marker_occurrences(
        *,
        section_id: str,
        content: str,
    ) -> tuple[CitationMarkerOccurrence, ...]:
        occurrences: list[CitationMarkerOccurrence] = []
        paragraph_start = 0
        paragraph_index = 0
        marker_index = 0
        boundaries = list(_PARAGRAPH_SEPARATOR.finditer(content or ""))

        for boundary_index in range(len(boundaries) + 1):
            paragraph_end = (
                boundaries[boundary_index].start()
                if boundary_index < len(boundaries)
                else len(content or "")
            )
            paragraph = (content or "")[paragraph_start:paragraph_end]
            if paragraph.strip():
                paragraph_index += 1
                paragraph_id = f"{section_id}_paragraph_{paragraph_index:03d}"
                for ordinal, match in enumerate(
                    _CITATION_PATTERN.finditer(paragraph),
                    start=1,
                ):
                    marker_index += 1
                    claim_id = f"{paragraph_id}_claim_{ordinal:03d}"
                    occurrences.append(
                        CitationMarkerOccurrence(
                            target_section_id=section_id,
                            target_paragraph_id=paragraph_id,
                            target_claim_id=claim_id,
                            marker_index=marker_index,
                            marker_ordinal=ordinal,
                            marker_start=paragraph_start + match.start(),
                            marker_end=paragraph_start + match.end(),
                            citation_id=match.group(1),
                            claim_text=_claim_for_marker(
                                paragraph,
                                match.start(),
                            ),
                        )
                    )
            if boundary_index < len(boundaries):
                paragraph_start = boundaries[boundary_index].end()
        return tuple(occurrences)

    @staticmethod
    def _binding_candidates(
        binding: CitationBindingSchema,
        occurrences: Sequence[CitationMarkerOccurrence],
    ) -> list[CitationMarkerOccurrence]:
        candidates = [
            occurrence
            for occurrence in occurrences
            if occurrence.target_section_id == binding.target_section_id
            and occurrence.citation_id == binding.citation_id
        ]
        if binding.target_marker_index is not None:
            candidates = [
                occurrence
                for occurrence in candidates
                if occurrence.marker_index == binding.target_marker_index
            ]
            if binding.marker_ordinal is not None:
                candidates = [
                    occurrence
                    for occurrence in candidates
                    if occurrence.marker_ordinal == binding.marker_ordinal
                ]
            if binding.target_marker_start is not None:
                candidates = [
                    occurrence
                    for occurrence in candidates
                    if occurrence.marker_start == binding.target_marker_start
                ]
            if binding.target_marker_end is not None:
                candidates = [
                    occurrence
                    for occurrence in candidates
                    if occurrence.marker_end == binding.target_marker_end
                ]
            candidates = [
                occurrence
                for occurrence in candidates
                if occurrence.target_paragraph_id == binding.target_paragraph_id
                and occurrence.target_claim_id == binding.target_claim_id
                and _normalized_claim(occurrence.claim_text)
                == _normalized_claim(binding.claim_text)
            ]
            return candidates

        normalized_binding_claim = _normalized_claim(binding.claim_text)
        claim_candidates = [
            occurrence
            for occurrence in candidates
            if _normalized_claim(occurrence.claim_text)
            == normalized_binding_claim
        ]
        if not claim_candidates:
            return []
        candidates = claim_candidates

        identity_candidates = [
            occurrence
            for occurrence in candidates
            if occurrence.target_paragraph_id == binding.target_paragraph_id
            and occurrence.target_claim_id == binding.target_claim_id
        ]
        if identity_candidates:
            candidates = identity_candidates
        return candidates if len(candidates) == 1 else []

    def _map_bindings(
        self,
        occurrences: Sequence[CitationMarkerOccurrence],
        bindings: Sequence[CitationBindingSchema],
    ) -> tuple[
        dict[str, list[CitationBindingSchema]],
        list[str],
        list[str],
    ]:
        bindings_by_occurrence: dict[
            str,
            list[CitationBindingSchema],
        ] = defaultdict(list)
        orphan_binding_ids: list[str] = []
        ambiguous_binding_ids: list[str] = []
        for binding in bindings:
            candidates = self._binding_candidates(binding, occurrences)
            if not candidates:
                orphan_binding_ids.append(binding.binding_id or "<blank>")
                continue
            if len(candidates) != 1:
                ambiguous_binding_ids.append(binding.binding_id or "<blank>")
                continue
            bindings_by_occurrence[candidates[0].identity].append(binding)
        return (
            dict(bindings_by_occurrence),
            orphan_binding_ids,
            ambiguous_binding_ids,
        )

    def reconcile_content(
        self,
        content: str,
        bindings: Iterable[CitationBindingSchema],
        *,
        section_id: str | None = None,
    ) -> str:
        binding_list = list(bindings)
        if section_id is None:
            section_ids = {
                binding.target_section_id
                for binding in binding_list
                if binding.target_section_id
            }
            section_id = (
                next(iter(section_ids))
                if len(section_ids) == 1
                else "__reconciled_section__"
            )
        occurrences = self.extract_marker_occurrences(
            section_id=section_id,
            content=content,
        )
        mapped, _, _ = self._map_bindings(occurrences, binding_list)
        verified_occurrence_ids = {
            occurrence.identity
            for occurrence in occurrences
            if len(mapped.get(occurrence.identity, [])) == 1
            and bool(
                (mapped[occurrence.identity][0].metadata or {}).get(
                    "grounding_verified"
                )
            )
        }

        reconciled = content
        for occurrence in reversed(occurrences):
            if occurrence.identity in verified_occurrence_ids:
                continue
            reconciled = (
                reconciled[:occurrence.marker_start]
                + reconciled[occurrence.marker_end:]
            )
        return reconciled

    def evaluate(
        self,
        *,
        sections: Iterable[Any],
        bindings: Iterable[CitationBindingSchema],
        full_text: str,
    ) -> CitationOccurrenceIntegrityResult:
        section_list = list(sections)
        binding_list = list(bindings)
        occurrences = [
            occurrence
            for section in section_list
            for occurrence in self.extract_marker_occurrences(
                section_id=section.section_id,
                content=str(section.content or ""),
            )
        ]
        mapped, orphan_binding_ids, ambiguous_binding_ids = self._map_bindings(
            occurrences,
            binding_list,
        )

        orphan_marker_ids = [
            occurrence.identity
            for occurrence in occurrences
            if not mapped.get(occurrence.identity)
        ]
        duplicate_marker_binding_ids = {
            occurrence.identity: [
                binding.binding_id or "<blank>"
                for binding in mapped.get(occurrence.identity, [])
            ]
            for occurrence in occurrences
            if len(mapped.get(occurrence.identity, [])) > 1
        }
        unverified_occurrence_ids = [
            occurrence.identity
            for occurrence in occurrences
            if len(mapped.get(occurrence.identity, [])) == 1
            and not bool(
                (mapped[occurrence.identity][0].metadata or {}).get(
                    "grounding_verified"
                )
            )
        ]

        section_marker_sequence = [
            occurrence.citation_id for occurrence in occurrences
        ]
        full_text_occurrences = self.extract_marker_occurrences(
            section_id="__document__",
            content=full_text or "",
        )
        full_text_marker_sequence = [
            occurrence.citation_id for occurrence in full_text_occurrences
        ]
        section_claim_sequence = [
            (
                occurrence.citation_id,
                _normalized_claim(occurrence.claim_text),
            )
            for occurrence in occurrences
        ]
        full_text_claim_sequence = [
            (
                occurrence.citation_id,
                _normalized_claim(occurrence.claim_text),
            )
            for occurrence in full_text_occurrences
        ]
        final_text_occurrences_match_sections = (
            full_text_claim_sequence == section_claim_sequence
        )

        checks = {
            "citation_marker_binding_bijection": not (
                orphan_marker_ids
                or orphan_binding_ids
                or ambiguous_binding_ids
                or duplicate_marker_binding_ids
            ),
            "citation_marker_occurrences_verified": not unverified_occurrence_ids,
            "citation_orphan_markers_absent": not orphan_marker_ids
            and final_text_occurrences_match_sections,
            "citation_orphan_bindings_absent": not (
                orphan_binding_ids or ambiguous_binding_ids
            ),
        }
        return CitationOccurrenceIntegrityResult(
            checks=checks,
            metadata={
                "citation_marker_occurrence_count": len(occurrences),
                "verified_binding_occurrence_count": sum(
                    1
                    for occurrence in occurrences
                    if len(mapped.get(occurrence.identity, [])) == 1
                    and bool(
                        (mapped[occurrence.identity][0].metadata or {}).get(
                            "grounding_verified"
                        )
                    )
                ),
                "orphan_marker_occurrence_ids": orphan_marker_ids,
                "orphan_binding_ids": orphan_binding_ids,
                "ambiguous_binding_ids": ambiguous_binding_ids,
                "duplicate_marker_binding_ids": duplicate_marker_binding_ids,
                "unverified_marker_occurrence_ids": unverified_occurrence_ids,
                "section_marker_sequence": section_marker_sequence,
                "full_text_marker_sequence": full_text_marker_sequence,
                "section_marker_claim_sequence": section_claim_sequence,
                "full_text_marker_claim_sequence": full_text_claim_sequence,
                "final_text_occurrences_match_sections": (
                    final_text_occurrences_match_sections
                ),
                "marker_counts": dict(Counter(section_marker_sequence)),
            },
        )
