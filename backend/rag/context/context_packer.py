# -*- coding: utf-8 -*-
"""
rag_template/context/context_packer.py
======================================

P3 context packer:
- Input: reranked retrieval_result_v2 list.
- Output: packed context string + selected results + citation metadata.

职责边界：
- 只做上下文预算控制和格式化，不调用 LLM。
- 第一版用字符预算，避免强依赖 tokenizer；后续可替换成 token budget。

v0.2 update:
- Add packing_strategy="lost_in_middle_aware".
- High-value chunks are placed near the beginning and end of the packed context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Set


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return value if isinstance(value, str) else str(value)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


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

    def to_dict(self) -> Dict[str, Any]:
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
    def build_citation(result: Dict[str, Any], context_rank: int) -> Dict[str, Any]:
        meta = result.get("metadata") or {}
        return {
            "context_rank": context_rank,
            "rank": result.get("rank"),
            "doc_id": result.get("doc_id"),
            "chunk_id": result.get("chunk_id"),
            "child_chunk_id": result.get("child_chunk_id"),
            "parent_chunk_id": result.get("parent_chunk_id"),
            "title": result.get("title"),
            "section": result.get("section"),
            "page_start": result.get("page_start"),
            "page_end": result.get("page_end"),
            "score": result.get("score"),
            "rerank_score": result.get("rerank_score"),
            "retrieval_sources": meta.get("retrieval_sources", []),
            "matched_child_chunk_ids": meta.get("matched_child_chunk_ids", []),
        }

    def _get_text(self, result: Dict[str, Any]) -> str:
        if self.text_field and result.get(self.text_field):
            return _safe_str(result.get(self.text_field))
        return _safe_str(result.get("text") or result.get("parent_text") or result.get("child_text"))

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
        if self.packing_strategy == "lost_in_middle_aware":
            return self._lost_in_middle_reorder(chunks)
        return chunks

    def _position_tag(self, context_rank: int, total: int) -> str:
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
    ) -> str:
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

        if page_start is not None and page_end is not None:
            page = f"{page_start}~{page_end}"
        elif page_start is not None:
            page = str(page_start)
        else:
            page = ""

        if self.include_metadata:
            tag_line = f"{position_tag}\n" if position_tag else ""
            header = (
                f"[资料 {context_rank}]\n"
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
            header = f"[资料 {context_rank}]{tag_line}\n"
        return f"{header}{text}".strip()

    @staticmethod
    def _mark_dropped(result: Dict[str, Any], reason: str) -> Dict[str, Any]:
        dropped = dict(result)
        dropped.pop("_pack_text", None)
        metadata = dict(dropped.get("metadata") or {})
        metadata["context_drop_reason"] = reason
        dropped["metadata"] = metadata
        return dropped

    def _collect_candidates(
        self,
        results: Iterable[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Filter and select top candidates before context budget packing.

        Dedup and max_items are applied before lost-in-middle reordering, so the
        selected evidence set is still based on original rerank order.
        """
        candidates: List[Dict[str, Any]] = []
        dropped: List[Dict[str, Any]] = []
        seen_parents: Set[str] = set()

        for result in results:
            if len(candidates) >= self.max_items:
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

    def pack(self, results: Iterable[Dict[str, Any]]) -> ContextPack:
        candidates, dropped = self._collect_candidates(results)
        ordered_candidates = self._apply_packing_strategy(candidates)

        selected: List[Dict[str, Any]] = []
        citations: List[Dict[str, Any]] = []
        parts: List[str] = []
        used_chars = 0
        total_items = len(ordered_candidates)

        for result in ordered_candidates:
            text = _safe_str(result.get("_pack_text")).strip()
            context_rank = len(selected) + 1
            item = self._format_item(
                result,
                context_rank=context_rank,
                text=text,
                total_items=total_items,
            )
            extra_len = len(item) + (2 if parts else 0)
            remaining = self.max_context_chars - used_chars

            if remaining <= 0:
                dropped.append(self._mark_dropped(result, "context_budget_exhausted"))
                continue

            if extra_len > remaining:
                # Allow a truncated first item, but avoid adding very tiny fragments later.
                if not parts and remaining > 300:
                    item = item[:remaining].rstrip() + "\n...[TRUNCATED]"
                    extra_len = len(item)
                else:
                    dropped.append(self._mark_dropped(result, "context_budget_overflow"))
                    continue

            clean_result = dict(result)
            clean_result.pop("_pack_text", None)
            original_rank = clean_result.get("rank")
            metadata = dict(clean_result.get("metadata") or {})
            metadata.setdefault("pre_context_rank", original_rank)
            clean_result["metadata"] = metadata
            clean_result["pre_context_rank"] = original_rank
            clean_result["context_rank"] = context_rank
            clean_result["rank"] = context_rank
            parts.append(item)
            selected.append(clean_result)
            citations.append(self.build_citation(clean_result, context_rank=context_rank))
            used_chars += extra_len

        context = "\n\n".join(parts)
        return ContextPack(
            context=context,
            selected_results=selected,
            citations=citations,
            dropped_results=dropped,
            max_context_chars=self.max_context_chars,
            used_chars=len(context),
            packing_strategy=self.packing_strategy,
        )
