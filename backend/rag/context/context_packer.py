# -*- coding: utf-8 -*-
"""P3 Context Packer：把重排后的父子检索结果压入 Prompt 证据预算。

Token 预算是主约束，字符预算仅是序列化安全上限。Packer 先按原重排顺序去重和截断候选
集合，再按优先级用最终真实渲染成本逐条准入；``lost_in_middle_aware`` 仅重排已准入
集合的呈现位置。试算和最终输出共用 Citation/渲染逻辑，只允许首个选中证据显式截断。
"""

from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Dict, Iterable, List, Set, TypeVar

from core.token_estimator import DeterministicTokenEstimator
from rag.evidence.citation_sources import CitationSource, citation_sources
from rag.schema.structured_evidence import build_structured_evidence


_MemberT = TypeVar("_MemberT")
_TRUNCATION_SUFFIX = "\n...[TRUNCATED]"


@dataclass(frozen=True)
class _PackMember:
    """A priority candidate and its frozen prompt-visible representation."""

    result: Dict[str, Any]
    text: str
    truncated: bool = False
    prefix_sha256: str | None = None


@dataclass(frozen=True)
class _RenderedPackPlan:
    """Exact presentation of every member; rendering never chooses membership."""

    context: str
    selected_results: List[Dict[str, Any]]
    citations: List[Dict[str, Any]]
    truncated_item_ids: List[str]
    tokens_used: int
    used_chars: int

    def fits(self, *, token_budget: int, char_budget: int) -> bool:
        return self.tokens_used <= token_budget and self.used_chars <= char_budget


@dataclass(frozen=True)
class _CanonicalRenderCost:
    """Maximum complete-output cost over the two supported renderers."""

    tokens: int
    chars: int

    def fits(self, *, token_budget: int, char_budget: int) -> bool:
        return self.tokens <= token_budget and self.chars <= char_budget


@dataclass(frozen=True, init=False)
class PreparedPromptProjection:
    """Owned prompt bytes and provenance; public reads never expose owned rows."""

    __identity: object = field(repr=False)
    __representation: tuple[str, bool, bool, int]
    __candidates: tuple[_PackMember, ...] = field(repr=False)
    __dropped: tuple[Dict[str, Any], ...] = field(repr=False)
    __renderers: tuple["ContextPacker", "ContextPacker"] = field(repr=False)
    __estimated_cost: _CanonicalRenderCost

    def __init__(self, *, representation, candidates, dropped, renderers):
        owned_candidates = tuple(deepcopy(candidates))
        owned_dropped = tuple(deepcopy(dropped))
        object.__setattr__(self, "_PreparedPromptProjection__identity", object())
        object.__setattr__(self, "_PreparedPromptProjection__representation", tuple(representation))
        object.__setattr__(self, "_PreparedPromptProjection__candidates", owned_candidates)
        object.__setattr__(self, "_PreparedPromptProjection__dropped", owned_dropped)
        object.__setattr__(self, "_PreparedPromptProjection__renderers", tuple(renderers))
        object.__setattr__(self, "_PreparedPromptProjection__estimated_cost",
                           self._max_render_cost(owned_candidates))

    @property
    def estimated_tokens(self) -> int:
        """Full prepared-candidate diagnostic; Gate uses admission cost instead."""
        return self.__estimated_cost.tokens

    @property
    def estimated_chars(self) -> int:
        return self.__estimated_cost.chars

    def _matches_representation(self, value) -> bool:
        return self.__representation == tuple(value)

    def _identity_token(self) -> object:
        return self.__identity

    def _candidate_snapshot(self) -> tuple[_PackMember, ...]:
        return tuple(deepcopy(self.__candidates))

    def _dropped_snapshot(self) -> tuple[Dict[str, Any], ...]:
        return tuple(deepcopy(self.__dropped))

    def _max_render_cost(self, members) -> _CanonicalRenderCost:
        # These are two render cost views of one proposed membership, never
        # admissions. Actual renderer includes dedup, citation aliases/digits,
        # headers/metadata, strategy tags, whole-output separators and bodies.
        plans = tuple(renderer._render_exact_membership(list(deepcopy(members)))
                      for renderer in self.__renderers)
        return _CanonicalRenderCost(tokens=max(plan.tokens_used for plan in plans),
                                    chars=max(plan.used_chars for plan in plans))


@dataclass(frozen=True, init=False)
class _PreparedPromptAdmission:
    """One identity-bound membership and its exact canonical budget proof."""

    __projection: PreparedPromptProjection = field(repr=False)
    __projection_identity: object = field(repr=False)
    __members: tuple[_PackMember, ...] = field(repr=False)
    __dropped: tuple[Dict[str, Any], ...] = field(repr=False)
    token_budget: int
    char_budget: int
    max_render_cost: _CanonicalRenderCost

    def __init__(self, *, projection, members, dropped, token_budget,
                 char_budget, max_render_cost):
        object.__setattr__(self, "_PreparedPromptAdmission__projection", projection)
        object.__setattr__(self, "_PreparedPromptAdmission__projection_identity", projection._identity_token())
        object.__setattr__(self, "_PreparedPromptAdmission__members", tuple(deepcopy(members)))
        object.__setattr__(self, "_PreparedPromptAdmission__dropped", tuple(deepcopy(dropped)))
        object.__setattr__(self, "token_budget", int(token_budget))
        object.__setattr__(self, "char_budget", int(char_budget))
        object.__setattr__(self, "max_render_cost", max_render_cost)

    @property
    def projection(self) -> PreparedPromptProjection:
        return self.__projection

    def _matches_projection(self, projection: PreparedPromptProjection) -> bool:
        return (self.__projection is projection
                and self.__projection_identity is projection._identity_token())

    def _member_snapshot(self) -> tuple[_PackMember, ...]:
        return tuple(deepcopy(self.__members))

    def _dropped_snapshot(self) -> tuple[Dict[str, Any], ...]:
        return tuple(deepcopy(self.__dropped))


def _safe_str(value: Any, default: str = "") -> str:
    """把可选标量规范为字符串。"""
    if value is None:
        return default
    return value if isinstance(value, str) else str(value)


def _safe_float(value: Any, default: float = 0.0) -> float:
    """安全规范化召回或重排分数。"""
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


@dataclass
class ContextPack:
    """最终上下文文本、选中/丢弃证据、引用和预算使用量的运行结果。"""

    context: str
    selected_results: List[Dict[str, Any]] = field(default_factory=list)
    citations: List[Dict[str, Any]] = field(default_factory=list)
    dropped_results: List[Dict[str, Any]] = field(default_factory=list)
    max_context_chars: int = 0
    used_chars: int = 0
    packing_strategy: str = "lost_in_middle_aware"
    token_budget: int = 0
    tokens_used: int = 0
    truncated_item_ids: List[str] = field(default_factory=list)

    @property
    def items(self) -> List[Dict[str, Any]]:
        return self.selected_results

    @property
    def rendered_text(self) -> str:
        return self.context

    def to_dict(self) -> Dict[str, Any]:
        """导出 Evidence Contract Builder 和运行捕获使用的兼容字典。"""
        return {
            "context": self.context,
            "selected_results": self.selected_results,
            "citations": self.citations,
            "dropped_results": self.dropped_results,
            "max_context_chars": self.max_context_chars,
            "used_chars": self.used_chars,
            "selected_count": len(self.selected_results),
            "dropped_count": len(self.dropped_results),
            "packing_strategy": self.packing_strategy,
            "items": self.selected_results,
            "rendered_text": self.context,
            "token_budget": self.token_budget,
            "tokens_used": self.tokens_used,
            "truncated_item_ids": list(self.truncated_item_ids),
        }


class ContextPacker:
    """Pack retrieval results into prompt-ready context.

    Parameters
    ----------
    packing_strategy:
        - "default": keep reranked order.
        - "lost_in_middle_aware": keep the strongest chunk at the beginning,
          the second strongest near the end, and alternate remaining chunks so
          high-value evidence avoids the middle of a long prompt.
    """

    def __init__(
        self,
        *,
        max_context_chars: int = 6000,
        max_items: int = 5,
        text_field: str = "text",
        dedup_parent: bool = True,
        include_metadata: bool = True,
        packing_strategy: str = "lost_in_middle_aware",
    ):
        """配置字符/条目上限、正文来源字段、父级去重和位置感知策略。"""
        if max_context_chars <= 0:
            raise ValueError("max_context_chars must be > 0")
        if max_items <= 0:
            raise ValueError("max_items must be > 0")

        allowed_strategies = {"default", "lost_in_middle_aware"}
        if packing_strategy not in allowed_strategies:
            raise ValueError(
                f"Unsupported packing_strategy={packing_strategy!r}. "
                f"Allowed: {sorted(allowed_strategies)}"
            )

        self.max_context_chars = int(max_context_chars)
        self.max_items = int(max_items)
        self.text_field = text_field
        self.dedup_parent = bool(dedup_parent)
        self.include_metadata = bool(include_metadata)
        self.packing_strategy = packing_strategy

    @staticmethod
    def build_citation(
        result: Dict[str, Any],
        *,
        context_rank: int,
        citation_id: str,
        source: CitationSource,
    ) -> Dict[str, Any]:
        """把一个实际命中子块绑定为引用来源，并附加其父级候选排名与召回血缘。"""
        meta = result.get("metadata") or {}
        return {
            "citation_id": citation_id,
            "context_rank": context_rank,
            "rank": result.get("rank"),
            "doc_id": result.get("doc_id"),
            "chunk_id": source.child_id,
            "child_chunk_id": source.child_id,
            "parent_chunk_id": result.get("parent_chunk_id"),
            "title": source.title or result.get("title"),
            "section": source.section or result.get("section"),
            "page_start": (
                source.page_start
                if source.page_start is not None
                else result.get("page_start")
            ),
            "page_end": (
                source.page_end
                if source.page_end is not None
                else result.get("page_end")
            ),
            "quote_text": source.quote_text,
            "score": result.get("score"),
            "rerank_score": result.get("rerank_score"),
            "retrieval_sources": meta.get("retrieval_sources", []),
            "matched_child_chunk_ids": meta.get("matched_child_chunk_ids", []),
            "expanded_from_parent_match": source.expanded_from_parent_match,
        }

    @staticmethod
    def _citation_sources(result: Dict[str, Any]) -> List[CitationSource]:
        return citation_sources(
            metadata=result.get("metadata") or {},
            fallback_child_id=(
                result.get("child_chunk_id")
                or result.get("matched_chunk_id")
                or result.get("chunk_id")
            ),
            fallback_quote_text=(
                result.get("match_text")
                or result.get("child_text")
                or result.get("text")
            ),
            fallback_title=result.get("title"),
            fallback_section=result.get("section"),
            fallback_page_start=result.get("page_start"),
            fallback_page_end=result.get("page_end"),
        )

    def _raw_text(self, result: Dict[str, Any]) -> str:
        """按配置字段读取原始生成上下文，并依次回退父块和子块正文。"""
        if self.text_field and result.get(self.text_field):
            return _safe_str(result.get(self.text_field))
        return _safe_str(
            result.get("text")
            or result.get("parent_text")
            or result.get("child_text")
        )

    def _prompt_projection(
        self,
        result: Dict[str, Any],
    ) -> tuple[str, Dict[str, Any]]:
        """Project governed evidence into the text that the writer may see.

        Retrieval and citation audit retain the raw parent/child text.  Only the
        prompt-facing projection is restricted here, after Evidence Governance
        has attached ``evidence_assessment_v2`` to the candidate.
        """
        raw_text = self._raw_text(result).strip()
        metadata = dict(result.get("metadata") or {})
        assessment = metadata.get("evidence_assessment_v2")
        if not isinstance(assessment, dict):
            return raw_text, {
                "prompt_projection_mode": "legacy_unprojected",
                "raw_fact_visibility": "full",
            }

        evidence_role = str(assessment.get("evidence_role") or "").strip()
        project_relation = str(assessment.get("project_relation") or "").strip()
        supported_claim_types = [
            str(item).strip()
            for item in assessment.get("supported_claim_types") or []
            if str(item or "").strip()
        ]
        forbidden_claim_types = {
            str(item).strip()
            for item in assessment.get("forbidden_claim_types") or []
            if str(item or "").strip()
        }
        current_project_claims = {
            "current_project_fact",
            "current_project_cost",
            "current_project_quantity",
            "current_project_schedule",
            "current_project_configuration",
        }

        if (
            evidence_role == "historical_case"
            or project_relation == "other_project"
        ) and forbidden_claim_types & current_project_claims:
            title = _safe_str(
                result.get("title") or result.get("section") or "历史案例"
            ).strip()
            allowed_usage = "、".join(supported_claim_types) or "reference_case"
            usable_reference = (
                assessment.get("eligibility") is True
                and assessment.get("relevance") in {"relevant", "partial"}
                and bool(supported_claim_types)
            )
            if usable_reference:
                projected = (
                    "【历史案例，仅供参考】\n"
                    f"来源章节：{title or '历史案例'}\n"
                    f"允许用途：{allowed_usage}。\n"
                    "该材料属于其他项目；具体参数可作为明确标注的建议、估算、规划假设或参考设计，"
                    "不得替代本项目权威事实，不得写成本项目已确认或已完成的事实。\n"
                    f"{raw_text}"
                )
                return projected, {
                    "prompt_projection_mode": "historical_reference_governed",
                    "raw_fact_visibility": "reference",
                }
            projected = (
                "【历史案例，仅供参考】\n"
                f"来源章节：{title or '历史案例'}\n"
                f"允许用途：{allowed_usage}。\n"
                "该材料属于其他项目，原文中的具体数量、容量、品牌、型号、"
                "设备配置和工期未向生成模型暴露；只能概括其方法、架构或案例经验，"
                "不得写成本项目既定事实。"
            )
            return projected, {
                "prompt_projection_mode": "historical_reference_restricted",
                "raw_fact_visibility": "restricted",
            }

        if evidence_role == "template_style":
            assessment_metadata = assessment.get("metadata")
            assessment_metadata = (
                assessment_metadata if isinstance(assessment_metadata, dict) else {}
            )
            structured = assessment_metadata.get("structured_evidence")
            structured = structured if isinstance(structured, dict) else {}
            template = structured.get("template")
            template = template if isinstance(template, dict) else {}
            title = _safe_str(
                template.get("section_title")
                or result.get("title")
                or result.get("section")
                or "模板章节"
            ).strip()
            required_fields = [
                str(item).strip()
                for item in template.get("required_fields") or []
                if str(item or "").strip()
            ]
            style_hints = [
                str(item).strip()
                for item in template.get("style_hints") or []
                if str(item or "").strip()
            ]
            lines = ["【模板结构，仅用于组织形式】", f"章节：{title}"]
            if required_fields:
                lines.append(f"建议字段：{'、'.join(required_fields)}")
            if style_hints:
                lines.append(f"写作提示：{'、'.join(style_hints)}")
            lines.append("模板示例内容不得作为项目事实或技术依据。")
            return "\n".join(lines), {
                "prompt_projection_mode": "template_structure_only",
                "raw_fact_visibility": "restricted",
            }

        return raw_text, {
            "prompt_projection_mode": "governed_full_text",
            "raw_fact_visibility": "full",
        }

    @staticmethod
    def _lost_in_middle_reorder(chunks: List[_MemberT]) -> List[_MemberT]:
        """Move high-ranked chunks to prompt edges.

        Input is assumed to be in descending relevance order after rerank.
        Example:
            [1, 2, 3, 4, 5, 6] -> [1, 3, 5, 6, 4, 2]

        The best chunk stays first, the second-best moves to the very end,
        the third stays near the front, and so on.
        """
        if len(chunks) <= 2:
            return chunks

        left: List[_MemberT] = []
        right: List[_MemberT] = []

        for idx, chunk in enumerate(chunks):
            if idx % 2 == 0:
                left.append(chunk)
            else:
                right.insert(0, chunk)

        return left + right

    def _apply_packing_strategy(self, chunks: List[_MemberT]) -> List[_MemberT]:
        """仅改变已选证据的呈现位置，不改变基于原重排顺序形成的证据集合。"""
        if self.packing_strategy == "lost_in_middle_aware":
            return self._lost_in_middle_reorder(chunks)
        return chunks

    def _position_tag(self, context_rank: int, total: int) -> str:
        """为位置感知策略标记开头、结尾和补充证据。"""
        if self.packing_strategy != "lost_in_middle_aware":
            return ""
        if total <= 1:
            return "【高优先级证据】"
        if context_rank == 1:
            return "【高优先级证据-开头】"
        if context_rank == total:
            return "【高优先级证据-结尾】"
        return "【补充证据】"

    def _format_item(
        self,
        result: Dict[str, Any],
        context_rank: int,
        text: str,
        total_items: int,
        citation_ids: List[str] | None = None,
    ) -> str:
        """将一个候选渲染为带引用 Marker 和可选来源 metadata 的上下文块。"""
        title = _safe_str(result.get("title"))
        section = _safe_str(result.get("section"))
        doc_id = _safe_str(result.get("doc_id"))
        parent_id = _safe_str(result.get("parent_chunk_id"))
        child_id = _safe_str(result.get("child_chunk_id"))
        page_start = result.get("page_start")
        page_end = result.get("page_end")
        score = _safe_float(result.get("score"))
        rerank_score = result.get("rerank_score")
        meta = result.get("metadata") or {}
        sources = meta.get("retrieval_sources", [])
        position_tag = self._position_tag(context_rank=context_rank, total=total_items)
        marker = " ".join(f"[{item}]" for item in citation_ids or [])
        if not marker:
            marker = f"[C{context_rank}]"

        if page_start is not None and page_end is not None:
            page = f"{page_start}~{page_end}"
        elif page_start is not None:
            page = str(page_start)
        else:
            page = ""

        if self.include_metadata:
            tag_line = f"{position_tag}\n" if position_tag else ""
            header = (
                f"{marker}\n"
                f"{tag_line}"
                f"doc_id: {doc_id}\n"
                f"parent_chunk_id: {parent_id}\n"
                f"child_chunk_id: {child_id}\n"
                f"title: {title}\n"
                f"section: {section}\n"
                f"page: {page}\n"
                f"score: {score}\n"
                f"rerank_score: {rerank_score}\n"
                f"retrieval_sources: {sources}\n"
                f"text:\n"
            )
        else:
            tag_line = f"\n{position_tag}" if position_tag else ""
            header = f"{marker}{tag_line}\n"
        return f"{header}{text}".strip()

    @staticmethod
    def _mark_dropped(result: Dict[str, Any], reason: str) -> Dict[str, Any]:
        """复制候选并写入明确的上下文丢弃原因，不污染原结果。"""
        dropped = dict(result)
        dropped.pop("_pack_text", None)
        dropped.pop("_prompt_projection_metadata", None)
        metadata = dict(dropped.get("metadata") or {})
        metadata["context_drop_reason"] = reason
        dropped["metadata"] = metadata
        return dropped

    def _collect_candidates(
        self,
        results: Iterable[Dict[str, Any]],
        *,
        max_items: int | None = None,
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Filter and select top candidates before context budget packing.

        Dedup and max_items are applied before lost-in-middle reordering, so the
        selected evidence set is still based on original rerank order.
        """
        candidates: List[Dict[str, Any]] = []
        dropped: List[Dict[str, Any]] = []
        seen_parents: Set[str] = set()

        item_limit = self.max_items if max_items is None else max(1, int(max_items))
        for result in results:
            if len(candidates) >= item_limit:
                dropped.append(self._mark_dropped(result, "max_items"))
                continue

            parent_id = _safe_str(result.get("parent_chunk_id"))
            if self.dedup_parent and parent_id and parent_id in seen_parents:
                dropped.append(self._mark_dropped(result, "duplicate_parent"))
                continue

            text, projection_metadata = self._prompt_projection(result)
            text = text.strip()
            if not text:
                dropped.append(self._mark_dropped(result, "empty_text"))
                continue

            normalized = dict(result)
            normalized["_pack_text"] = text
            normalized["_prompt_projection_metadata"] = projection_metadata
            candidates.append(normalized)

            if parent_id:
                seen_parents.add(parent_id)

        return candidates, dropped

    def _render_exact_membership(
        self,
        members: List[_PackMember],
    ) -> _RenderedPackPlan:
        """Render the entire membership with real positions and citation costs."""
        presentation = self._apply_packing_strategy(list(members))
        if sorted(map(id, presentation)) != sorted(map(id, members)):
            raise RuntimeError("context presentation changed admitted membership")

        selected: List[Dict[str, Any]] = []
        citations: List[Dict[str, Any]] = []
        parts: List[str] = []
        truncated_item_ids: List[str] = []
        citation_id_by_child: Dict[str, str] = {}
        for context_rank, member in enumerate(presentation, 1):
            result = member.result
            sources = self._citation_sources(result)
            item_citation_ids: List[str] = []
            for citation_source in sources:
                citation_id = citation_id_by_child.get(citation_source.child_id)
                if citation_id is None:
                    citation_id = f"C{len(citation_id_by_child) + 1}"
                    citation_id_by_child[citation_source.child_id] = citation_id
                if citation_id not in item_citation_ids:
                    item_citation_ids.append(citation_id)

            packed_text = member.text
            if member.truncated:
                prefix = packed_text.removesuffix(_TRUNCATION_SUFFIX)
                if sha256(prefix.encode("utf-8")).hexdigest() != member.prefix_sha256:
                    raise RuntimeError("context packer changed a frozen prefix")
            parts.append(
                self._format_item(
                    result,
                    context_rank=context_rank,
                    text=packed_text,
                    total_items=len(members),
                    citation_ids=item_citation_ids,
                )
            )
            clean_result = dict(result)
            clean_result.pop("_pack_text", None)
            projection_metadata = dict(
                clean_result.pop("_prompt_projection_metadata", {}) or {}
            )
            original_rank = clean_result.get("rank")
            metadata = dict(clean_result.get("metadata") or {})
            metadata.update(projection_metadata)
            metadata.setdefault("pre_context_rank", original_rank)
            metadata["context_truncated"] = member.truncated
            metadata["prompt_citation_ids"] = list(item_citation_ids)
            metadata["prompt_visible_char_count"] = len(packed_text)
            metadata["prompt_visible_token_count"] = (
                DeterministicTokenEstimator.estimate(packed_text)
            )
            if member.truncated:
                metadata["truncated_source_id"] = _safe_str(
                    result.get("parent_chunk_id")
                    or result.get("child_chunk_id")
                    or result.get("chunk_id")
                )
                metadata["truncation_reason"] = "token_or_character_budget"
                truncated_id = _safe_str(
                    result.get("parent_chunk_id")
                    or result.get("chunk_id")
                    or result.get("id")
                )
                if truncated_id:
                    truncated_item_ids.append(truncated_id)
            clean_result["prompt_visible_text"] = packed_text
            # EvidenceMapper consumes precisely the prompt-visible projection.
            clean_result["context_text"] = packed_text
            clean_result["metadata"] = metadata
            clean_result["pre_context_rank"] = original_rank
            clean_result["context_rank"] = context_rank
            clean_result["rank"] = context_rank
            selected.append(clean_result)
            for citation_source in sources:
                citation_id = citation_id_by_child[citation_source.child_id]
                if any(item["citation_id"] == citation_id for item in citations):
                    continue
                citations.append(
                    self.build_citation(
                        clean_result,
                        context_rank=context_rank,
                        citation_id=citation_id,
                        source=citation_source,
                    )
                )

        context = "\n\n".join(parts)
        return _RenderedPackPlan(
            context=context,
            selected_results=selected,
            citations=citations,
            truncated_item_ids=truncated_item_ids,
            tokens_used=DeterministicTokenEstimator.estimate(context),
            used_chars=len(context),
        )

    def _projection_representation(self, *, max_items: int | None = None):
        item_limit = self.max_items if max_items is None else max(1, int(max_items))
        return (self.text_field, self.dedup_parent, self.include_metadata, item_limit)

    def _effective_budgets(self, *, token_budget: int | None = None,
                           char_budget: int | None = None) -> tuple[int, int]:
        # Existing limit normalization, now reused by admission and consumption.
        tokens = max(1, int(token_budget) if token_budget is not None else self.max_context_chars)
        chars = max(1, min(self.max_context_chars,
                          int(char_budget) if char_budget is not None else self.max_context_chars))
        return tokens, chars

    def _assert_prepared_representation(self, projection, *, max_items=None):
        if not isinstance(projection, PreparedPromptProjection):
            raise ValueError("prepared projection type is invalid")
        if not projection._matches_representation(self._projection_representation(max_items=max_items)):
            raise ValueError("prepared projection representation differs from this packer")

    def prepare_projection(self, results: Iterable[Dict[str, Any]], *,
                           max_items: int | None = None) -> PreparedPromptProjection:
        # Materialize once; callers and later public output cannot mutate owned
        # headers, provenance, nested metadata or the governed prompt body.
        materialized = deepcopy(list(results))
        candidates, dropped = self._collect_candidates(materialized, max_items=max_items)
        members = tuple(_PackMember(result=row, text=_safe_str(row.get("_pack_text")))
                        for row in candidates)
        renderers = []
        for strategy in ("default", "lost_in_middle_aware"):
            renderer = copy(self)
            renderer.packing_strategy = strategy
            renderers.append(renderer)
        return PreparedPromptProjection(
            representation=self._projection_representation(max_items=max_items),
            candidates=members, dropped=dropped, renderers=tuple(renderers))

    def _find_exact_first_selected_truncation(
        self, member: _PackMember, *, projection: PreparedPromptProjection,
        token_budget: int, char_budget: int,
    ) -> _PackMember | None:
        """Same guarded maximal-prefix search against the canonical envelope."""
        if char_budget <= 300 or token_budget <= 64:
            return None
        if build_structured_evidence(member.result).representation_type in {
            "table_row", "policy_clause", "template_section",
        }:
            return None
        low, high = 0, len(member.text)
        best: _PackMember | None = None
        while low <= high:
            middle = (low + high) // 2
            prefix = member.text[:middle].rstrip()
            trial_member = _PackMember(result=member.result,
                text=prefix + _TRUNCATION_SUFFIX, truncated=True,
                prefix_sha256=sha256(prefix.encode("utf-8")).hexdigest())
            cost = projection._max_render_cost((trial_member,))
            if cost.fits(token_budget=token_budget, char_budget=char_budget):
                best, low = trial_member, middle + 1
            else:
                high = middle - 1
        return best

    def admit_projection(self, projection: PreparedPromptProjection, *,
                         token_budget: int | None = None,
                         max_items: int | None = None,
                         char_budget: int | None = None) -> _PreparedPromptAdmission:
        self._assert_prepared_representation(projection, max_items=max_items)
        effective_tokens, effective_chars = self._effective_budgets(
            token_budget=token_budget, char_budget=char_budget)
        admitted: List[_PackMember] = []
        dropped = list(projection._dropped_snapshot())
        admitted_cost = projection._max_render_cost(())
        for member in projection._candidate_snapshot():
            trial_cost = projection._max_render_cost((*admitted, member))
            if trial_cost.fits(token_budget=effective_tokens, char_budget=effective_chars):
                admitted.append(member)
                admitted_cost = trial_cost
                continue
            if not admitted:
                truncated = self._find_exact_first_selected_truncation(member,
                    projection=projection, token_budget=effective_tokens,
                    char_budget=effective_chars)
                if truncated is not None:
                    admitted.append(truncated)
                    admitted_cost = projection._max_render_cost(tuple(admitted))
                    continue
            # No re-truncation of an already admitted prefix, no new drop words.
            reason = ("token_budget_exhausted"
                      if admitted_cost.chars >= effective_chars
                      or admitted_cost.tokens >= effective_tokens
                      else "token_budget_overflow")
            dropped.append(self._mark_dropped(member.result, reason))
        if not admitted_cost.fits(token_budget=effective_tokens, char_budget=effective_chars):
            raise RuntimeError("canonical context admission exceeded its budget")
        return _PreparedPromptAdmission(projection=projection, members=admitted,
            dropped=dropped, token_budget=effective_tokens, char_budget=effective_chars,
            max_render_cost=admitted_cost)

    def pack(self, results: Iterable[Dict[str, Any]], *,
             token_budget: int | None = None, max_items: int | None = None,
             char_budget: int | None = None,
             prepared_projection: PreparedPromptProjection | None = None,
             prepared_admission: _PreparedPromptAdmission | None = None) -> ContextPack:
        effective_tokens, effective_chars = self._effective_budgets(
            token_budget=token_budget, char_budget=char_budget)
        if prepared_admission is not None:
            if not isinstance(prepared_admission, _PreparedPromptAdmission):
                raise ValueError("prepared admission type is invalid")
            projection = (prepared_projection if prepared_projection is not None
                          else prepared_admission.projection)
            self._assert_prepared_representation(projection, max_items=max_items)
            if not prepared_admission._matches_projection(projection):
                raise ValueError("prepared admission belongs to a different projection")
            if (prepared_admission.token_budget, prepared_admission.char_budget) != (effective_tokens, effective_chars):
                raise ValueError("prepared admission budget differs from the render request")
            admission = prepared_admission
        else:
            projection = (prepared_projection if prepared_projection is not None
                          else self.prepare_projection(results, max_items=max_items))
            admission = self.admit_projection(projection, token_budget=effective_tokens,
                max_items=max_items, char_budget=effective_chars)
        # Exact old complete renderer does only permutation and byte rendering.
        final = self._render_exact_membership(list(admission._member_snapshot()))
        if (final.tokens_used != DeterministicTokenEstimator.estimate(final.context)
                or final.used_chars != len(final.context)
                or final.tokens_used > admission.max_render_cost.tokens
                or final.used_chars > admission.max_render_cost.chars
                or not final.fits(token_budget=effective_tokens, char_budget=effective_chars)):
            raise RuntimeError("prepared context rendering violated canonical admission")
        # Existing shallow rendering projections are detached deeply before any
        # public lists/dicts escape; mutating pack output cannot taint reuse.
        selected, citations, dropped, truncated_ids = deepcopy((
            final.selected_results, final.citations,
            list(admission._dropped_snapshot()), final.truncated_item_ids))
        return ContextPack(context=final.context, selected_results=selected,
            citations=citations, dropped_results=dropped,
            max_context_chars=effective_chars, used_chars=final.used_chars,
            packing_strategy=self.packing_strategy, token_budget=effective_tokens,
            tokens_used=final.tokens_used, truncated_item_ids=truncated_ids)
