"""文档级引用身份、去重和标记重映射。

多次文档检索、章节检索和恢复检索都可能返回局部 ``C1``。注册表依据来源文档、Chunk、
页码和引文等业务身份统一分配文档级 ``C1/C2/...``，并同步重写上下文、证据契约与正文
标记；它不负责检索、生成或 LLM 修复。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple

from observability.trace_summary import canonical_sha256
from schemas.citation import CitationSchema
from schemas.rag import RAGContextSchema, RAGEvidenceContractSchema, RetrievedChunkSchema


_MARKER_PATTERN = re.compile(r"\[([^\[\]]+)\]")


class DocumentCitationRegistry:
    """跨多次 RAG 调用维护稳定、去重且可恢复的文档引用编号。"""

    def __init__(self) -> None:
        self._by_key: Dict[str, CitationSchema] = {}
        self._ordered: List[CitationSchema] = []

    @staticmethod
    def _identity(citation: CitationSchema) -> str:
        payload = {
            "source_document_id": citation.source_document_id or citation.doc_id,
            "file_id": citation.file_id,
            "parent_chunk_id": citation.parent_chunk_id,
            "child_chunk_id": citation.child_chunk_id,
            "chunk_id": citation.chunk_id,
            "table_id": citation.table_id,
            "row_ids": list(citation.row_ids),
            "page_start": citation.page_start,
            "page_end": citation.page_end,
            "quote_text": " ".join((citation.quote_text or "").split()),
            "title": citation.title,
            "section": citation.section,
        }
        if not any(value for value in payload.values()):
            payload["original_citation_id"] = citation.citation_id
        return canonical_sha256(payload)

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
                    update={"citation_id": citation_id, "extra": extra}
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
            self._by_key[key] = citation.model_copy(deep=True)
            self._ordered.append(citation.model_copy(deep=True))
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
        """把一次规范化证据包中的局部 ID 全量替换为文档级稳定 ID。

        重映射覆盖引用对象、上下文文本及结构化 Evidence Contract；返回的 normalized
        payload 仍保留同一协议形状，供后续章节生成和数据采集直接使用。
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

        raw_contract = (context.extra or {}).get("evidence_contract")
        if isinstance(raw_contract, dict):
            contract_payload = dict(raw_contract)
            contract_payload["citations"] = [
                item.model_dump() for item in remapped_citations
            ]
            items = []
            for raw_item in contract_payload.get("items") or []:
                item = dict(raw_item)
                item["citation_ids"] = list(
                    dict.fromkeys(
                        id_map.get(value, value)
                        for value in item.get("citation_ids") or []
                    )
                )
                items.append(item)
            contract_payload["items"] = items
            raw_context = dict(contract_payload.get("context") or {})
            raw_context["context_text"] = self._replace_markers(
                str(raw_context.get("context_text") or ""), id_map
            )
            raw_context["used_context_chars"] = len(raw_context["context_text"])
            contract_payload["context"] = raw_context
            contract_payload.setdefault("extra", {})
            contract_payload["extra"] = {
                **dict(contract_payload.get("extra") or {}),
                "citation_registry_scope": scope,
                "citation_id_map": id_map,
            }
            contract = RAGEvidenceContractSchema.model_validate(contract_payload)
            remapped_context = contract.context.model_copy(
                update={
                    "extra": {
                        **dict(remapped_context.extra or {}),
                        "evidence_contract": contract.model_dump(),
                        "evidence_contract_sha256": canonical_sha256(
                            contract.model_dump()
                        ),
                        "lineage": contract.lineage.model_dump(),
                        "retrieval_scope": scope,
                    }
                }
            )
            normalized = dict(normalized)
            normalized["evidence"] = contract.model_dump()
        else:
            remapped_context.extra = {
                **dict(remapped_context.extra or {}),
                "retrieval_scope": scope,
                "citation_id_map": id_map,
            }

        normalized = dict(normalized)
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
