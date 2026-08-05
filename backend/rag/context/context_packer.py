# -*- coding: utf-8 -*-
"""P3 Context Packer：把重排后的父子检索结果压入 Prompt 证据预算。

Token 预算是主约束，字符预算仅是序列化安全上限。Packer 先按原重排顺序去重和截断候选
集合，再可选执行 ``lost_in_middle_aware`` 重排，将高价值证据放在上下文开头和结尾；
最后构造稳定 Citation、记录丢弃原因，并只允许首个证据在预算不足时做显式截断。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Set

from context_manager.token_estimator import DeterministicTokenEstimator
from rag.evidence.citation_sources import CitationSource, citation_sources


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
    def _lost_in_middle_reorder(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Move high-ranked chunks to prompt edges.

        Input is assumed to be in descending relevance order after rerank.
        Example:
            [1, 2, 3, 4, 5, 6] -> [1, 3, 5, 6, 4, 2]

        The best chunk stays first, the second-best moves to the very end,
        the third stays near the front, and so on.
        """
        if len(chunks) <= 2:
            return chunks

        left: List[Dict[str, Any]] = []
        right: List[Dict[str, Any]] = []

        for idx, chunk in enumerate(chunks):
            if idx % 2 == 0:
                left.append(chunk)
            else:
                right.insert(0, chunk)

        return left + right

    def _apply_packing_strategy(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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

    def pack(
        self,
        results: Iterable[Dict[str, Any]],
        *,
        token_budget: int | None = None,
        max_items: int | None = None,
        char_budget: int | None = None,
    ) -> ContextPack:
        """在条目、字符和估算 Token 三重预算下构造最终上下文。

        Citation ID 按实际子块首次进入上下文的顺序分配，同一子块不会重复建引用。若整个
        上下文为空，第一条证据可二分搜索最大可容纳前缀并附加 ``[TRUNCATED]``；后续
        证据不插入极小碎片，而是带原因丢弃。返回前再次验证 Token 预算不被突破。
        """
        effective_token_budget = max(
            1,
            int(token_budget) if token_budget is not None else self.max_context_chars,
        )
        effective_char_budget = max(
            1,
            min(
                self.max_context_chars,
                int(char_budget)
                if char_budget is not None
                else self.max_context_chars,
            ),
        )
        candidates, dropped = self._collect_candidates(results, max_items=max_items)
        ordered_candidates = self._apply_packing_strategy(candidates)

        selected: List[Dict[str, Any]] = []
        citations: List[Dict[str, Any]] = []
        parts: List[str] = []
        truncated_item_ids: List[str] = []
        citation_id_by_child: Dict[str, str] = {}
        total_items = len(ordered_candidates)

        for result in ordered_candidates:
            sources = self._citation_sources(result)
            provisional_ids: Dict[str, str] = {}
            item_citation_ids: List[str] = []
            for source in sources:
                citation_id = citation_id_by_child.get(source.child_id)
                if citation_id is None:
                    citation_id = provisional_ids.get(source.child_id)
                if citation_id is None:
                    citation_id = (
                        f"C{len(citation_id_by_child) + len(provisional_ids) + 1}"
                    )
                    provisional_ids[source.child_id] = citation_id
                if citation_id not in item_citation_ids:
                    item_citation_ids.append(citation_id)
            text = _safe_str(result.get("_pack_text")).strip()
            packed_text = text
            was_truncated = False
            context_rank = len(selected) + 1
            item = self._format_item(
                result,
                context_rank=context_rank,
                text=text,
                total_items=total_items,
                citation_ids=item_citation_ids,
            )
            separator = "\n\n" if parts else ""
            candidate_context = separator.join([*parts, item]) if parts else item
            remaining = effective_char_budget - len("\n\n".join(parts))
            remaining_tokens = effective_token_budget - (
                DeterministicTokenEstimator.estimate("\n\n".join(parts))
            )

            if remaining <= 0 or remaining_tokens <= 0:
                dropped.append(self._mark_dropped(result, "token_budget_exhausted"))
                continue

            if (
                len(candidate_context) > effective_char_budget
                or DeterministicTokenEstimator.estimate(candidate_context)
                > effective_token_budget
            ):
                # Allow a truncated first item, but avoid adding very tiny fragments later.
                if not parts and remaining > 300 and remaining_tokens > 64:
                    suffix = "\n...[TRUNCATED]"
                    low = 0
                    high = len(text)
                    best_item = ""
                    best_text = ""
                    while low <= high:
                        middle = (low + high) // 2
                        candidate_text = text[:middle].rstrip() + suffix
                        candidate = self._format_item(
                            result,
                            context_rank=context_rank,
                            text=candidate_text,
                            total_items=total_items,
                            citation_ids=item_citation_ids,
                        )
                        if (
                            len(candidate) <= effective_char_budget
                            and DeterministicTokenEstimator.estimate(candidate)
                            <= effective_token_budget
                        ):
                            best_item = candidate
                            best_text = candidate_text
                            low = middle + 1
                        else:
                            high = middle - 1
                    if not best_item:
                        dropped.append(
                            self._mark_dropped(result, "token_budget_overflow")
                        )
                        continue
                    item = best_item
                    packed_text = best_text
                    was_truncated = True
                    truncated_item_ids.append(
                        _safe_str(
                            result.get("parent_chunk_id")
                            or result.get("chunk_id")
                            or result.get("id")
                        )
                    )
                else:
                    dropped.append(self._mark_dropped(result, "token_budget_overflow"))
                    continue

            clean_result = dict(result)
            clean_result.pop("_pack_text", None)
            projection_metadata = dict(
                clean_result.pop("_prompt_projection_metadata", {}) or {}
            )
            original_rank = clean_result.get("rank")
            metadata = dict(clean_result.get("metadata") or {})
            metadata.update(projection_metadata)
            metadata.setdefault("pre_context_rank", original_rank)
            metadata["context_truncated"] = was_truncated
            metadata["prompt_citation_ids"] = list(item_citation_ids)
            metadata["prompt_visible_char_count"] = len(packed_text)
            metadata["prompt_visible_token_count"] = (
                DeterministicTokenEstimator.estimate(packed_text)
            )
            if was_truncated:
                metadata["truncated_source_id"] = _safe_str(
                    result.get("parent_chunk_id")
                    or result.get("child_chunk_id")
                    or result.get("chunk_id")
                )
                metadata["truncation_reason"] = "token_or_character_budget"
            clean_result["prompt_visible_text"] = packed_text
            # ``context_text`` is the compatibility projection consumed by
            # EvidenceMapper.  It must equal the text actually visible in the
            # prompt, never the full parent chunk retained for retrieval audit.
            clean_result["context_text"] = packed_text
            clean_result["metadata"] = metadata
            clean_result["pre_context_rank"] = original_rank
            clean_result["context_rank"] = context_rank
            clean_result["rank"] = context_rank
            parts.append(item)
            selected.append(clean_result)
            citation_id_by_child.update(provisional_ids)
            for source in sources:
                citation_id = citation_id_by_child[source.child_id]
                if any(
                    item.get("citation_id") == citation_id for item in citations
                ):
                    continue
                citations.append(
                    self.build_citation(
                        clean_result,
                        context_rank=context_rank,
                        citation_id=citation_id,
                        source=source,
                    )
                )

        context = "\n\n".join(parts)
        tokens_used = DeterministicTokenEstimator.estimate(context)
        if tokens_used > effective_token_budget:
            raise RuntimeError("context packer exceeded its token budget")
        return ContextPack(
            context=context,
            selected_results=selected,
            citations=citations,
            dropped_results=dropped,
            max_context_chars=effective_char_budget,
            used_chars=len(context),
            packing_strategy=self.packing_strategy,
            token_budget=effective_token_budget,
            tokens_used=tokens_used,
            truncated_item_ids=[item for item in truncated_item_ids if item],
        )
