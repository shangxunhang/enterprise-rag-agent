# =============================================================================
# 中文阅读说明：文档级引用注册表，负责跨多次 RAG 调用分配稳定引用编号并统一 remap。
# 不负责检索、生成或 LLM 修复。
# =============================================================================
"""Document-wide citation identity, deduplication and remapping."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple

from observability.trace_summary import canonical_sha256
from schemas.citation import CitationSchema
from schemas.rag import RAGContextSchema, RAGEvidenceContractSchema, RetrievedChunkSchema


_MARKER_PATTERN = re.compile(r"\[([^\[\]]+)\]")


class DocumentCitationRegistry:
    """Allocate stable document-wide citation ids across multiple RAG calls."""

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

    def all(self) -> List[CitationSchema]:
        return list(self._ordered)

    @staticmethod
    def _replace_markers(text: str, id_map: Dict[str, str]) -> str:
        if not text or not id_map:
            return text

        def replace(match: re.Match[str]) -> str:
            old = match.group(1)
            return f"[{id_map.get(old, old)}]"

        return _MARKER_PATTERN.sub(replace, text)

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
        """Apply stable document-wide ids to one normalized evidence bundle."""

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
