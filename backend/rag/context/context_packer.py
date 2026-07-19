# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：_safe_str、_safe_float、ContextPack、ContextPacker。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
rag_template/context/context_packer.py
======================================

P3 context packer:
- Input: reranked retrieval_result_v2 list.
- Output: packed context string + selected results + citation metadata.

职责边界：
- 只做上下文预算控制和格式化，不调用 LLM。
- Token 预算是主约束，字符预算仅作为序列化安全上限。

v0.2 update:
- Add packing_strategy="lost_in_middle_aware".
- High-value chunks are placed near the beginning and end of the packed context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Set

from context_manager.token_estimator import DeterministicTokenEstimator
from rag.evidence.citation_sources import CitationSource, citation_sources


# 阅读注释（函数）：处理 safe str 相关逻辑。
def _safe_str(value: Any, default: str = "") -> str:
    """处理 safe str 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：isinstance, str。
    """
    if value is None:
        return default
    return value if isinstance(value, str) else str(value)


# 阅读注释（函数）：处理 safe float 相关逻辑。
def _safe_float(value: Any, default: float = 0.0) -> float:
    """处理 safe float 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        float

    阅读提示:
        主要直接调用：float。
    """
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


# 阅读注释（类）：封装 上下文 pack，集中封装相关状态、依赖和行为。
@dataclass
class ContextPack:
    """Packed context result."""

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

    # 阅读注释（函数）：把 ContextPack 转换为 字典。
    def to_dict(self) -> Dict[str, Any]:
        """把 ContextPack 转换为 字典。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：len。
        """
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


# 阅读注释（类）：封装 上下文 packer，集中封装相关状态、依赖和行为。
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

    # 阅读注释（函数）：初始化 ContextPacker，保存运行所需的依赖、配置或状态。
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
        """初始化 ContextPacker，保存运行所需的依赖、配置或状态。

        参数:
            max_context_chars: max 上下文 chars，具体约束请结合类型标注和调用方确认。
            max_items: max 数据项集合，具体约束请结合类型标注和调用方确认。
            text_field: 文本 field，具体约束请结合类型标注和调用方确认。
            dedup_parent: dedup 父块，具体约束请结合类型标注和调用方确认。
            include_metadata: include 元数据，具体约束请结合类型标注和调用方确认。
            packing_strategy: packing strategy，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：ValueError, sorted, int, bool。
        """
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

    # 阅读注释（函数）：构建 引用。
    @staticmethod
    def build_citation(
        result: Dict[str, Any],
        *,
        context_rank: int,
        citation_id: str,
        source: CitationSource,
    ) -> Dict[str, Any]:
        """构建 引用。

        参数:
            result: 待处理的结果对象。
            context_rank: 上下文 rank，具体约束请结合类型标注和调用方确认。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：result.get, meta.get。
        """
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

    # 阅读注释（函数）：获取 文本。
    def _get_text(self, result: Dict[str, Any]) -> str:
        """获取 文本。

        参数:
            result: 待处理的结果对象。

        返回:
            str

        阅读提示:
            主要直接调用：result.get, _safe_str。
        """
        if self.text_field and result.get(self.text_field):
            return _safe_str(result.get(self.text_field))
        return _safe_str(result.get("text") or result.get("parent_text") or result.get("child_text"))

    # 阅读注释（函数）：处理 lost in middle reorder 相关逻辑。
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

    # 阅读注释（函数）：应用 packing strategy。
    def _apply_packing_strategy(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """应用 packing strategy。

        参数:
            chunks: chunks，具体约束请结合类型标注和调用方确认。

        返回:
            List[Dict[str, Any]]

        阅读提示:
            主要直接调用：self._lost_in_middle_reorder。
        """
        if self.packing_strategy == "lost_in_middle_aware":
            return self._lost_in_middle_reorder(chunks)
        return chunks

    # 阅读注释（函数）：处理 position tag 相关逻辑。
    def _position_tag(self, context_rank: int, total: int) -> str:
        """处理 position tag 相关逻辑。

        参数:
            context_rank: 上下文 rank，具体约束请结合类型标注和调用方确认。
            total: total，具体约束请结合类型标注和调用方确认。

        返回:
            str
        """
        if self.packing_strategy != "lost_in_middle_aware":
            return ""
        if total <= 1:
            return "【高优先级证据】"
        if context_rank == 1:
            return "【高优先级证据-开头】"
        if context_rank == total:
            return "【高优先级证据-结尾】"
        return "【补充证据】"

    # 阅读注释（函数）：格式化 数据项。
    def _format_item(
        self,
        result: Dict[str, Any],
        context_rank: int,
        text: str,
        total_items: int,
        citation_ids: List[str] | None = None,
    ) -> str:
        """格式化 数据项。

        参数:
            result: 待处理的结果对象。
            context_rank: 上下文 rank，具体约束请结合类型标注和调用方确认。
            text: 待处理文本。
            total_items: total 数据项集合，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：_safe_str, result.get, _safe_float, meta.get, self._position_tag, str, strip。
        """
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

    # 阅读注释（函数）：处理 mark dropped 相关逻辑。
    @staticmethod
    def _mark_dropped(result: Dict[str, Any], reason: str) -> Dict[str, Any]:
        """处理 mark dropped 相关逻辑。

        参数:
            result: 待处理的结果对象。
            reason: reason，具体约束请结合类型标注和调用方确认。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：dict, dropped.pop, dropped.get。
        """
        dropped = dict(result)
        dropped.pop("_pack_text", None)
        metadata = dict(dropped.get("metadata") or {})
        metadata["context_drop_reason"] = reason
        dropped["metadata"] = metadata
        return dropped

    # 阅读注释（函数）：收集 candidates。
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

            text = self._get_text(result).strip()
            if not text:
                dropped.append(self._mark_dropped(result, "empty_text"))
                continue

            normalized = dict(result)
            normalized["_pack_text"] = text
            candidates.append(normalized)

            if parent_id:
                seen_parents.add(parent_id)

        return candidates, dropped

    # 阅读注释（函数）：压缩并组装 ContextPacker。
    def pack(
        self,
        results: Iterable[Dict[str, Any]],
        *,
        token_budget: int | None = None,
        max_items: int | None = None,
        char_budget: int | None = None,
    ) -> ContextPack:
        """压缩并组装 ContextPacker。

        参数:
            results: 待处理的结果集合。

        返回:
            ContextPack

        阅读提示:
            主要直接调用：self._collect_candidates, self._apply_packing_strategy, len, strip, _safe_str, result.get, self._format_item, dropped.append。
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
            original_rank = clean_result.get("rank")
            metadata = dict(clean_result.get("metadata") or {})
            metadata.setdefault("pre_context_rank", original_rank)
            if was_truncated:
                metadata["context_truncated"] = True
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
