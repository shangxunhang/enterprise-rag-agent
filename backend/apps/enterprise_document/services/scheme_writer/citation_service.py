# =============================================================================
# 中文阅读说明：确定性引用处理服务。
# 只负责引用标记解析、绑定、支持性评分、严格过滤和确定性 marker 插入；不调用 LLM。
# =============================================================================
"""Deterministic citation binding and grounding verification."""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Tuple

from schemas.citation import CitationBindingSchema, CitationSchema

from .constants import (
    CITATION_PATTERN as _CITATION_PATTERN,
    GENERIC_CITATION_TOKENS as _GENERIC_CITATION_TOKENS,
    MIN_CITATION_LONG_TOKEN_OVERLAP as _MIN_CITATION_LONG_TOKEN_OVERLAP,
    MIN_CITATION_SUPPORT_SCORE as _MIN_CITATION_SUPPORT_SCORE,
    MIN_CITATION_TOKEN_OVERLAP as _MIN_CITATION_TOKEN_OVERLAP,
)


class CitationService:
    """Pure deterministic citation/grounding policy; never invokes a model."""

    @staticmethod
    def _claim_for_marker(
        paragraph: str,
        marker: str,
        marker_position: Optional[int] = None,
    ) -> str:
        if marker_position is None:
            marker_position = paragraph.find(marker)
        if marker_position < 0:
            return paragraph.strip()

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

    @staticmethod
    def bind_citations(
        document_id: str,
        section_id: str,
        content: str,
        citations: List[CitationSchema],
    ) -> List[CitationBindingSchema]:
        by_id = {item.citation_id: item for item in citations}
        bindings: list[CitationBindingSchema] = []
        paragraphs = [
            item.strip() for item in re.split(r"\n\s*\n", content) if item.strip()
        ]
        for paragraph_index, paragraph in enumerate(paragraphs, start=1):
            paragraph_id = f"{section_id}_paragraph_{paragraph_index:03d}"
            for marker_index, marker_match in enumerate(
                _CITATION_PATTERN.finditer(paragraph), start=1
            ):
                citation_id = marker_match.group(1)
                citation = by_id.get(citation_id)
                if citation is None:
                    continue
                marker = f"[{citation_id}]"
                claim = CitationService._claim_for_marker(
                    paragraph,
                    marker,
                    marker_position=marker_match.start(),
                )
                claim_id = f"{paragraph_id}_claim_{marker_index:03d}"
                bindings.append(
                    CitationBindingSchema(
                        binding_id=(
                            f"binding_{section_id}_{paragraph_index:03d}_{marker_index:03d}"
                        ),
                        citation_id=citation_id,
                        target_document_id=document_id,
                        target_section_id=section_id,
                        target_paragraph_id=paragraph_id,
                        target_claim_id=claim_id,
                        source_document_id=citation.source_document_id or citation.doc_id,
                        source_chunk_id=(
                            citation.chunk_id
                            or citation.child_chunk_id
                            or citation.parent_chunk_id
                        ),
                        source_parent_chunk_id=citation.parent_chunk_id,
                        claim_text=claim,
                        quote_text=citation.quote_text,
                        confidence=citation.confidence,
                    )
                )
        return bindings

    @staticmethod
    def citation_match_tokens(text: str) -> set[str]:
        normalized = unicodedata.normalize("NFKC", text or "").lower()
        tokens = set(re.findall(r"[a-z0-9][a-z0-9_.+\-/]{1,}", normalized))
        for segment in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
            for size in (2, 3, 4):
                for index in range(max(0, len(segment) - size + 1)):
                    tokens.add(segment[index : index + size])
        return {
            token
            for token in tokens
            if token not in _GENERIC_CITATION_TOKENS
        }

    @classmethod
    def _citation_support_score(
        cls,
        claim_text: str,
        citation: CitationSchema,
    ) -> float:
        claim_tokens = cls.citation_match_tokens(claim_text)
        evidence_text = "\n".join(
            item
            for item in (
                citation.title,
                citation.section,
                citation.quote_text,
                citation.summary,
            )
            if item
        )
        evidence_tokens = cls.citation_match_tokens(evidence_text)
        if not claim_tokens or not evidence_tokens:
            return 0.0

        overlap = claim_tokens & evidence_tokens
        coverage = len(overlap) / max(1, len(claim_tokens))
        long_phrase_count = sum(1 for token in overlap if len(token) >= 4)
        exact_phrase_bonus = min(0.25, long_phrase_count * 0.05)
        return min(1.0, coverage + exact_phrase_bonus)

    @classmethod
    def _binding_is_supported(
        cls,
        binding: CitationBindingSchema,
        citations_by_id: Dict[str, CitationSchema],
    ) -> bool:
        citation = citations_by_id.get(binding.citation_id)
        if citation is None:
            return False
        score = cls._citation_support_score(binding.claim_text, citation)
        claim_tokens = cls.citation_match_tokens(binding.claim_text)
        evidence_tokens = cls.citation_match_tokens(
            "\n".join(
                item
                for item in (citation.title, citation.section, citation.quote_text)
                if item
            )
        )
        overlap = claim_tokens & evidence_tokens
        long_overlap_count = sum(1 for token in overlap if len(token) >= 4)
        claim_plain = cls.strip_known_citation_markers(
            binding.claim_text,
            citations_by_id.keys(),
        ).strip()
        evidence_plain = "\n".join(
            item
            for item in (citation.title, citation.section, citation.quote_text)
            if item
        ).strip()
        exact_support = len(claim_plain) >= 8 and claim_plain in evidence_plain
        return exact_support or (
            score >= _MIN_CITATION_SUPPORT_SCORE
            and len(overlap) >= _MIN_CITATION_TOKEN_OVERLAP
            and long_overlap_count >= _MIN_CITATION_LONG_TOKEN_OVERLAP
        )

    @classmethod
    def supported_bindings(
        cls,
        bindings: List[CitationBindingSchema],
        citations: List[CitationSchema],
    ) -> List[CitationBindingSchema]:
        by_id = {item.citation_id: item for item in citations}
        supported: list[CitationBindingSchema] = []
        for binding in bindings:
            citation = by_id.get(binding.citation_id)
            if citation is None or not cls._binding_is_supported(binding, by_id):
                continue
            score = cls._citation_support_score(binding.claim_text, citation)
            metadata = dict(binding.metadata or {})
            metadata.update(
                {
                    "grounding_verified": True,
                    "grounding_score": score,
                    "grounding_policy": "lexical_strict_v2",
                }
            )
            supported.append(binding.model_copy(update={"metadata": metadata}))
        return supported

    @classmethod
    def insert_deterministic_citations(
        cls,
        content: str,
        citations: List[CitationSchema],
        *,
        max_bindings: int = 3,
    ) -> Tuple[str, List[Tuple[str, str, float]]]:
        if not content.strip() or not citations:
            return content, []

        known_ids = [item.citation_id for item in citations]
        cleaned_content = content
        for citation_id in known_ids:
            cleaned_content = cleaned_content.replace(f"[{citation_id}]", "")

        lines = cleaned_content.splitlines()
        candidates: list[Tuple[float, int, CitationSchema, str]] = []
        for line_index, raw_line in enumerate(lines):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            claim = re.sub(r"^[\-+*\d.、（）()\s]+", "", stripped)
            claim = claim.replace("**", "").strip()
            if len(claim) < 10:
                continue
            for citation in citations:
                score = cls._citation_support_score(claim, citation)
                claim_tokens = cls.citation_match_tokens(claim)
                evidence_tokens = cls.citation_match_tokens(
                    "\n".join(
                        item
                        for item in (
                            citation.title,
                            citation.section,
                            citation.quote_text,
                        )
                        if item
                    )
                )
                overlap = claim_tokens & evidence_tokens
                long_overlap_count = sum(1 for token in overlap if len(token) >= 4)
                evidence_plain = "\n".join(
                    item
                    for item in (
                        citation.title,
                        citation.section,
                        citation.quote_text,
                    )
                    if item
                )
                exact_support = len(claim) >= 8 and claim in evidence_plain
                supported = exact_support or (
                    score >= _MIN_CITATION_SUPPORT_SCORE
                    and len(overlap) >= _MIN_CITATION_TOKEN_OVERLAP
                    and long_overlap_count >= _MIN_CITATION_LONG_TOKEN_OVERLAP
                )
                if supported:
                    candidates.append((score, line_index, citation, claim))

        selected: list[Tuple[str, str, float]] = []
        used_lines: set[int] = set()
        for score, line_index, citation, claim in sorted(
            candidates,
            key=lambda item: item[0],
            reverse=True,
        ):
            if len(selected) >= max_bindings:
                break
            if line_index in used_lines:
                continue
            marker = f"[{citation.citation_id}]"
            lines[line_index] = lines[line_index].rstrip() + marker
            used_lines.add(line_index)
            selected.append((citation.citation_id, claim, score))

        return "\n".join(lines), selected

    @staticmethod
    def strip_known_citation_markers(
        text: str,
        citation_ids: Iterable[str],
    ) -> str:
        cleaned = text
        for citation_id in citation_ids:
            cleaned = cleaned.replace(f"[{citation_id}]", "")
        return re.sub(r"\s+", "", cleaned)
