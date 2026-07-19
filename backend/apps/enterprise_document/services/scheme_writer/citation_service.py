# =============================================================================
# 中文阅读说明：引用处理服务：引用标记、绑定、支持性过滤、修复及引用注册表维护。
# 主要定义：CitationService。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Generated from the stable v7.5.1 SchemeWriter behavior."""


import json
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Tuple

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from schemas.citation import CitationBindingSchema, CitationSchema
from schemas.model import ModelResponseSchema
from .model_service import SectionModelService
from .prompt_service import SectionPromptService

from .constants import (
    CITATION_PATTERN as _CITATION_PATTERN,
    GENERIC_CITATION_TOKENS as _GENERIC_CITATION_TOKENS,
    MIN_CITATION_LONG_TOKEN_OVERLAP as _MIN_CITATION_LONG_TOKEN_OVERLAP,
    MIN_CITATION_SUPPORT_SCORE as _MIN_CITATION_SUPPORT_SCORE,
    MIN_CITATION_TOKEN_OVERLAP as _MIN_CITATION_TOKEN_OVERLAP,
)


# 阅读注释（类）：封装 引用 服务，封装一组可复用的业务能力。
class CitationService:
    """封装 引用 服务，封装一组可复用的业务能力。"""

    def __init__(
        self,
        *,
        model_service: SectionModelService,
        prompt_service: SectionPromptService,
    ) -> None:
        self.model_service = model_service
        self.prompt_service = prompt_service
    # 阅读注释（函数）：处理 claim for marker 相关逻辑。
    @staticmethod
    def _claim_for_marker(
        paragraph: str,
        marker: str,
        marker_position: Optional[int] = None,
    ) -> str:
        """Return the claim immediately preceding a citation marker.

        A marker is normally written after the sentence-ending punctuation,
        e.g. ``事实描述。[C1]``. Looking for the sentence *containing* the
        marker therefore associates it with the following sentence. We instead
        inspect the text before the marker and select its last non-empty line or
        sentence.
        """

        if marker_position is None:
            marker_position = paragraph.find(marker)
        if marker_position < 0:
            return paragraph.strip()

        preceding = paragraph[:marker_position].rstrip()
        if not preceding:
            return paragraph.strip()

        # Markdown list items are line-oriented, while normal prose is
        # sentence-oriented. First isolate the last non-empty line, then the
        # last sentence within that line.
        non_empty_lines = [line.strip() for line in preceding.splitlines() if line.strip()]
        candidate_line = non_empty_lines[-1] if non_empty_lines else preceding
        sentence_parts = [
            item.strip()
            for item in re.split(r"(?<=[。！？!?；;])", candidate_line)
            if item.strip()
        ]
        return sentence_parts[-1] if sentence_parts else candidate_line.strip()

    # 阅读注释（函数）：处理 bind citations 相关逻辑。
    @staticmethod
    def _bind_citations(
        document_id: str,
        section_id: str,
        content: str,
        citations: List[CitationSchema],
    ) -> List[CitationBindingSchema]:
        """处理 bind citations 相关逻辑。

        参数:
            document_id: 文档 标识，具体约束请结合类型标注和调用方确认。
            section_id: 章节 标识，具体约束请结合类型标注和调用方确认。
            content: 待处理内容。
            citations: 引用信息集合。

        返回:
            List[CitationBindingSchema]

        阅读提示:
            主要直接调用：item.strip, re.split, enumerate, _CITATION_PATTERN.finditer, marker_match.group, by_id.get, CitationService._claim_for_marker, marker_match.start。
        """
        by_id = {item.citation_id: item for item in citations}
        bindings: list[CitationBindingSchema] = []
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", content) if item.strip()]
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
                    paragraph, marker, marker_position=marker_match.start()
                )
                claim_id = f"{paragraph_id}_claim_{marker_index:03d}"
                bindings.append(
                    CitationBindingSchema(
                        binding_id=f"binding_{section_id}_{paragraph_index:03d}_{marker_index:03d}",
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

    # 阅读注释（函数）：处理 引用 match tokens 相关逻辑。
    @staticmethod
    def _citation_match_tokens(text: str) -> set[str]:
        """Build deterministic lexical tokens for citation support checks.

        Citation linking must not depend on a small generation model copying a
        marker correctly.  The linker therefore uses normalized Chinese
        n-grams plus Latin/number terms to identify claims that are directly
        grounded in one retrieved evidence item.
        """

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

    # 阅读注释（函数）：处理 引用 support score 相关逻辑。
    @classmethod
    def _citation_support_score(cls, claim_text: str, citation: CitationSchema) -> float:
        """Return a conservative lexical grounding score in the range [0, 1]."""

        claim_tokens = cls._citation_match_tokens(claim_text)
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
        evidence_tokens = cls._citation_match_tokens(evidence_text)
        if not claim_tokens or not evidence_tokens:
            return 0.0

        overlap = claim_tokens & evidence_tokens
        coverage = len(overlap) / max(1, len(claim_tokens))
        long_phrase_count = sum(1 for token in overlap if len(token) >= 4)
        exact_phrase_bonus = min(0.25, long_phrase_count * 0.05)
        return min(1.0, coverage + exact_phrase_bonus)

    # 阅读注释（函数）：处理 绑定关系 is supported 相关逻辑。
    @classmethod
    def _binding_is_supported(
        cls,
        binding: CitationBindingSchema,
        citations_by_id: Dict[str, CitationSchema],
    ) -> bool:
        """处理 绑定关系 is supported 相关逻辑。

        参数:
            binding: 绑定关系，具体约束请结合类型标注和调用方确认。
            citations_by_id: citations by 标识，具体约束请结合类型标注和调用方确认。

        返回:
            bool

        阅读提示:
            主要直接调用：citations_by_id.get, cls._citation_support_score, cls._citation_match_tokens, join, sum, len, strip, cls._strip_known_citation_markers。
        """
        citation = citations_by_id.get(binding.citation_id)
        if citation is None:
            return False
        score = cls._citation_support_score(binding.claim_text, citation)
        claim_tokens = cls._citation_match_tokens(binding.claim_text)
        evidence_tokens = cls._citation_match_tokens(
            "\n".join(
                item
                for item in (citation.title, citation.section, citation.quote_text)
                if item
            )
        )
        overlap = claim_tokens & evidence_tokens
        long_overlap_count = sum(1 for token in overlap if len(token) >= 4)
        claim_plain = cls._strip_known_citation_markers(
            binding.claim_text,
            citations_by_id.keys(),
        ).strip()
        evidence_plain = "\n".join(
            item
            for item in (citation.title, citation.section, citation.quote_text)
            if item
        ).strip()
        exact_support = (
            len(claim_plain) >= 8
            and claim_plain in evidence_plain
        )
        return exact_support or (
            score >= _MIN_CITATION_SUPPORT_SCORE
            and len(overlap) >= _MIN_CITATION_TOKEN_OVERLAP
            and long_overlap_count >= _MIN_CITATION_LONG_TOKEN_OVERLAP
        )

    # 阅读注释（函数）：处理 supported bindings 相关逻辑。
    @classmethod
    def _supported_bindings(
        cls,
        bindings: List[CitationBindingSchema],
        citations: List[CitationSchema],
    ) -> List[CitationBindingSchema]:
        """处理 supported bindings 相关逻辑。

        参数:
            bindings: bindings，具体约束请结合类型标注和调用方确认。
            citations: 引用信息集合。

        返回:
            List[CitationBindingSchema]

        阅读提示:
            主要直接调用：by_id.get, cls._binding_is_supported, cls._citation_support_score, dict, metadata.update, supported.append, binding.model_copy。
        """
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

    # 阅读注释（函数）：处理 insert deterministic citations 相关逻辑。
    @classmethod
    def _insert_deterministic_citations(
        cls,
        content: str,
        citations: List[CitationSchema],
        *,
        max_bindings: int = 3,
    ) -> Tuple[str, List[Tuple[str, str, float]]]:
        """Insert citation markers onto the strongest evidence-backed lines.

        This is a deterministic fallback used before the LLM citation-repair
        call. It only appends a marker to an existing line and never rewrites
        the generated business text.
        """

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
                claim_tokens = cls._citation_match_tokens(claim)
                evidence_tokens = cls._citation_match_tokens(
                    "\n".join(
                        item
                        for item in (citation.title, citation.section, citation.quote_text)
                        if item
                    )
                )
                overlap = claim_tokens & evidence_tokens
                long_overlap_count = sum(1 for token in overlap if len(token) >= 4)
                evidence_plain = "\n".join(
                    item
                    for item in (citation.title, citation.section, citation.quote_text)
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
            candidates, key=lambda item: item[0], reverse=True
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

    # 阅读注释（函数）：处理 strip known 引用 markers 相关逻辑。
    @staticmethod
    def _strip_known_citation_markers(
        text: str,
        citation_ids: Iterable[str],
    ) -> str:
        """处理 strip known 引用 markers 相关逻辑。

        参数:
            text: 待处理文本。
            citation_ids: 引用 标识集合，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：cleaned.replace, re.sub。
        """
        cleaned = text
        for citation_id in citation_ids:
            cleaned = cleaned.replace(f"[{citation_id}]", "")
        return re.sub(r"\s+", "", cleaned)

    # 阅读注释（函数）：修复 章节 citations。
    def _repair_section_citations(
        self,
        shared_state: SharedStateSchema,
        *,
        content: str,
        section_id: str,
        section_title: str,
        project_input: ProjectInputSchema,
        citations: List[CitationSchema],
    ) -> Tuple[str, Optional[ModelResponseSchema]]:
        """Ask the model to insert valid citation markers without rewriting text.

        The repaired text is accepted only when:
        1. at least one marker maps to an available CitationSchema; and
        2. after removing those markers, the text is nearly identical to the
           original content. This prevents a citation-repair call from silently
           changing business facts.
        """

        if not content.strip() or not citations:
            return content, None

        citation_ids = [item.citation_id for item in citations]
        prompt = (
            f"请为下面的‘{section_title}’章节补充知识库引用标记。\n"
            "严格要求：\n"
            "1. 只能在被证据直接支持的句子末尾插入引用标记；\n"
            "2. 只能使用引用目录中存在的标记，例如[C1]；\n"
            "3. 不得增加、删除、改写、重排任何正文文字；\n"
            "4. 不得给没有证据支持的句子强行添加引用；\n"
            "5. 只输出插入引用后的完整原文，不要解释。\n\n"
            f"引用目录：\n{self.prompt_service._citation_catalog(citations)}\n\n"
            f"原始正文：\n{content}"
        )
        response = self.model_service._call_model(
            shared_state,
            prompt=prompt,
            section_id=section_id,
            section_title=section_title,
            project_input=project_input,
            available_citation_ids=citation_ids,
            purpose="scheme_citation_repair",
            suffix="_citation_repair",
        )
        if not response.success or not response.content.strip():
            return content, response

        candidate = response.content.strip()
        candidate_bindings = self._supported_bindings(
            self._bind_citations(
                document_id="citation_repair_validation",
                section_id=section_id,
                content=candidate,
                citations=citations,
            ),
            citations,
        )
        if not candidate_bindings:
            print(
                f"[CitationRepair] REJECT section={section_title} "
                "reason=unsupported_binding",
                flush=True,
            )
            return content, response

        original_plain = self._strip_known_citation_markers(content, citation_ids)
        candidate_plain = self._strip_known_citation_markers(candidate, citation_ids)
        similarity = SequenceMatcher(None, original_plain, candidate_plain).ratio()
        if similarity < 0.97:
            print(
                f"[CitationRepair] REJECT section={section_title} reason=text_changed "
                f"similarity={similarity:.4f}",
                flush=True,
            )
            return content, response

        print(
            f"[CitationRepair] ACCEPT section={section_title} "
            f"bindings={len(candidate_bindings)} similarity={similarity:.4f}",
            flush=True,
        )
        return candidate, response

    # 阅读注释（函数）：处理 regenerate 章节 from 证据 相关逻辑。
    def _regenerate_section_from_evidence(
        self,
        shared_state: SharedStateSchema,
        *,
        original_content: str,
        section_id: str,
        section_title: str,
        project_input: ProjectInputSchema,
        citations: List[CitationSchema],
    ) -> ModelResponseSchema:
        """Rewrite one failed section using only explicit child evidence.

        This is the real section-repair path.  It is intentionally different
        from citation-marker repair: if the original prose itself is not
        supported, merely appending a marker would create a false citation.
        """

        prompt = (
            f"“{section_title}”章节未通过 Claim-Evidence 校验，请重写该章节。\n"
            "强制要求：\n"
            "1. 只能使用项目输入和下列引用目录中直接出现的信息；\n"
            "2. 每个来自知识库的确定性事实必须在句末标注对应引用；\n"
            "3. 不得把常识、经验或推测包装成项目事实；\n"
            "4. 证据未覆盖的内容必须写为‘待补充’或‘需项目方确认’；\n"
            "5. 不得保留原文中没有证据支持的安全措施、资源数量、指标或承诺；\n"
            "6. 只输出重写后的完整章节正文。\n\n"
            f"项目输入：\n{json.dumps(project_input.model_dump(), ensure_ascii=False, indent=2)}\n\n"
            f"引用目录：\n{self.prompt_service._citation_catalog(citations)}\n\n"
            f"未通过校验的原始正文（仅供识别需要修正的范围，不得照抄无依据内容）：\n"
            f"{original_content}"
        )
        return self.model_service._call_model(
            shared_state,
            prompt=prompt,
            section_id=section_id,
            section_title=section_title,
            project_input=project_input,
            available_citation_ids=[item.citation_id for item in citations],
            purpose="scheme_grounded_regeneration",
            suffix="_grounded_regeneration",
        )
