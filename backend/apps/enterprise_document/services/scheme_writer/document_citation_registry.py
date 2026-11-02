"""文档级引用身份、去重和标记重映射。

多次文档检索、章节检索和恢复检索都可能返回局部 ``C1``。注册表依据来源文档、Chunk、
页码和引文等业务身份统一分配文档级 ``C1/C2/...``，并生成 Application-owned 上下文与
引用渲染投影；它不修改或重建 canonical EvidenceBundle。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple

from schemas.citation import CitationSchema, citation_source_identity
from schemas.rag import RAGContextSchema, RetrievedChunkSchema


_MARKER_PATTERN = re.compile(r"\[([^\[\]]+)\]")


class DocumentCitationRegistry:
    """跨多次 RAG 调用维护稳定、去重且可恢复的文档引用编号。"""

    def __init__(self) -> None:
        self._by_key: Dict[str, CitationSchema] = {}
        self._ordered: List[CitationSchema] = []

    @staticmethod
    def _identity(citation: CitationSchema) -> str:
        """Reuse the canonical Citation source identity produced at the RAG boundary."""

        return citation.citation_ref or citation_source_identity(citation)

    def register(
        self,
        citations: Iterable[CitationSchema],
        *,
        scope: str,
        query: str,
    ) -> Tuple[List[CitationSchema], Dict[str, str]]:
        """注册一批局部引用，返回文档级引用和旧 ID 到新 ID 的映射。

        同一业务来源再次出现时复用既有编号，只追加检索范围与 Query 血缘；不同来源即使
        局部 citation_id 相同，也会获得新的文档级编号。
        """
        remapped: List[CitationSchema] = []
        id_map: Dict[str, str] = {}
        for citation in citations:
            key = self._identity(citation)
            existing = self._by_key.get(key)
            if existing is None:
                citation_id = f"C{len(self._ordered) + 1}"
                extra = dict(citation.extra or {})
                extra.update(
                    {
                        "original_citation_id": citation.citation_id,
                        "retrieval_scopes": [scope],
                        "retrieval_queries": [query],
                        "citation_identity_sha256": key,
                    }
                )
                existing = citation.model_copy(
                    update={
                        "citation_ref": citation.citation_ref or key,
                        "citation_id": citation_id,
                        "extra": extra,
                    }
                )
                self._by_key[key] = existing
                self._ordered.append(existing)
            else:
                extra = dict(existing.extra or {})
                scopes = list(extra.get("retrieval_scopes") or [])
                queries = list(extra.get("retrieval_queries") or [])
                if scope not in scopes:
                    scopes.append(scope)
                if query not in queries:
                    queries.append(query)
                updated = existing.model_copy(
                    update={
                        "extra": {
                            **extra,
                            "retrieval_scopes": scopes,
                            "retrieval_queries": queries,
                        }
                    }
                )
                self._by_key[key] = updated
                index = next(
                    i
                    for i, item in enumerate(self._ordered)
                    if item.citation_id == existing.citation_id
                )
                self._ordered[index] = updated
                existing = updated
            id_map[citation.citation_id] = existing.citation_id
            remapped.append(existing)
        return remapped, id_map

    def restore(self, citations: Iterable[CitationSchema]) -> None:
        """从 Checkpoint 安全的序列化引用恢复编号顺序和业务身份索引。"""

        self._by_key.clear()
        self._ordered.clear()
        seen_ids: set[str] = set()
        for citation in citations:
            if citation.citation_id in seen_ids:
                continue
            key = self._identity(citation)
            restored = citation.model_copy(
                update={"citation_ref": citation.citation_ref or key},
                deep=True,
            )
            self._by_key[key] = restored
            self._ordered.append(restored.model_copy(deep=True))
            seen_ids.add(citation.citation_id)

    @classmethod
    def from_citations(
        cls,
        citations: Iterable[CitationSchema],
    ) -> "DocumentCitationRegistry":
        registry = cls()
        registry.restore(citations)
        return registry

    def all(self) -> List[CitationSchema]:
        return [item.model_copy(deep=True) for item in self._ordered]

    @staticmethod
    def _replace_markers(text: str, id_map: Dict[str, str]) -> str:
        if not text or not id_map:
            return text

        def replace(match: re.Match[str]) -> str:
            old = match.group(1)
            return f"[{id_map.get(old, old)}]"

        return _MARKER_PATTERN.sub(replace, text)

    @classmethod
    def replace_markers(cls, text: str, id_map: Dict[str, str]) -> str:
        """Public deterministic marker remap used during ordered fan-in."""

        return cls._replace_markers(text, id_map)

    def remap_bundle(
        self,
        *,
        context: RAGContextSchema,
        chunks: List[RetrievedChunkSchema],
        citations: List[CitationSchema],
        normalized: Dict[str, Any],
        scope: str,
        query: str,
    ) -> Tuple[
        RAGContextSchema,
        List[RetrievedChunkSchema],
        List[CitationSchema],
        Dict[str, Any],
    ]:
        """Project bundle-local citation labels into document-level rendering labels.

        This method mutates only the registry and Application projections.  Canonical
        Evidence citations/items/context/hash are never rewritten or reconstructed.
        """

        remapped_citations, id_map = self.register(
            citations,
            scope=scope,
            query=query,
        )
        remapped_citations = list(
            {item.citation_id: item for item in remapped_citations}.values()
        )
        remapped_context_text = self._replace_markers(context.context_text, id_map)
        remapped_context = context.model_copy(
            update={
                "context_text": remapped_context_text,
                "used_context_chars": len(remapped_context_text),
            }
        )

        remapped_context.extra = {
            **dict(remapped_context.extra or {}),
            "retrieval_scope": scope,
            "citation_id_map": id_map,
        }

        normalized = {
            key: value
            for key, value in dict(normalized).items()
            if key != "evidence"
        }
        normalized["context"] = remapped_context.model_dump()
        normalized["citations"] = [item.model_dump() for item in remapped_citations]
        normalized["retrieved_chunks"] = [item.model_dump() for item in chunks]
        normalized.setdefault("extra", {})
        normalized["extra"] = {
            **dict(normalized.get("extra") or {}),
            "retrieval_scope": scope,
            "citation_id_map": id_map,
        }
        return remapped_context, chunks, remapped_citations, normalized
