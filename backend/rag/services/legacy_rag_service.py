"""Canonical RAG service backed by the legacy rag-template runtime.

The service is intentionally thin. Request mapping, backend invocation and
response/evidence mapping are isolated under ``rag.adapters.legacy``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from contracts.rag import BaseRAGService
from core.error_factory import ErrorFactory
from core.runtime.timing import MonotonicTimer, Timer, elapsed_ms
from rag.adapters.legacy.backend import LegacyRAGBackend
from rag.adapters.legacy.coercion import as_bool, as_int, as_str_list
from rag.adapters.legacy.evidence_mapper import LegacyEvidenceMapper
from rag.adapters.legacy.request_mapper import LegacyRAGRequestMapper
from rag.adapters.legacy.result_mapper import LegacyRAGResultMapper
from schemas.citation import CitationSchema
from schemas.rag import (
    RAGContextSchema,
    RAGToolInputSchema,
    RAGToolOutputSchema,
    RAGTraceSchema,
    RetrievedChunkSchema,
)


class LegacyRAGService(BaseRAGService):
    service_name = "LegacyRAGService"

    def __init__(
        self,
        rag_project_root: str | Path,
        generate_answer: bool = False,
        skip_rerank: bool = False,
        pipeline_config_file: str | Path | None = None,
        *,
        backend: LegacyRAGBackend | None = None,
        request_mapper: LegacyRAGRequestMapper | None = None,
        result_mapper: LegacyRAGResultMapper | None = None,
        error_factory: ErrorFactory | None = None,
        timer: Timer | None = None,
    ) -> None:
        self.rag_project_root = Path(rag_project_root).resolve()
        self.backend_root = self.rag_project_root / "backend"
        self.generate_answer = generate_answer
        self.skip_rerank = skip_rerank
        self.backend = backend or LegacyRAGBackend(
            self.rag_project_root,
            generate_answer=generate_answer,
            skip_rerank=skip_rerank,
            pipeline_config_file=pipeline_config_file,
        )
        self.request_mapper = request_mapper or LegacyRAGRequestMapper(
            default_generate_answer=generate_answer
        )
        self.result_mapper = result_mapper or LegacyRAGResultMapper()
        self.evidence_mapper: LegacyEvidenceMapper = self.result_mapper.evidence
        self.error_factory = error_factory or ErrorFactory()
        self.timer = timer or MonotonicTimer()

    @property
    def _rag_tool(self) -> Any | None:
        return self.backend._rag_tool

    @_rag_tool.setter
    def _rag_tool(self, value: Any | None) -> None:
        self.backend._rag_tool = value

    # Compatibility methods retained for tests and legacy callers.
    def _build_rag_tool(self) -> Any:
        return self.backend.tool()

    @staticmethod
    def _to_context_text(item: Dict[str, Any]) -> str:
        return LegacyEvidenceMapper.context_text(item)

    @staticmethod
    def _to_match_text(item: Dict[str, Any]) -> str:
        return LegacyEvidenceMapper.match_text(item)

    @staticmethod
    def _as_str_list(value: Any) -> List[str]:
        return as_str_list(value)

    @staticmethod
    def _as_bool(value: Any, default: bool = False) -> bool:
        return as_bool(value, default)

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        return as_int(value, default)

    @classmethod
    def _extract_query_expansion(
        cls,
        rag_result: Dict[str, Any],
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        return LegacyRAGResultMapper.query_expansion(rag_result, data)

    @classmethod
    def _extract_rewritten_queries(
        cls,
        query_expansion: Dict[str, Any],
        tool_input: Dict[str, Any],
    ) -> List[str]:
        return as_str_list(query_expansion.get("rewritten_queries")) or as_str_list(
            tool_input.get("rewritten_queries")
        )

    @staticmethod
    def _extract_strategy_payload(
        rag_result: Dict[str, Any],
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        return LegacyRAGResultMapper.strategy_payload(rag_result, data)

    def _convert_retrieved_chunks(
        self,
        contexts: List[Dict[str, Any]],
    ) -> List[RetrievedChunkSchema]:
        return self.evidence_mapper.chunks(contexts)

    @staticmethod
    def _build_context(
        retrieved_chunks: List[RetrievedChunkSchema],
        max_context_chars: int,
    ) -> RAGContextSchema:
        return LegacyEvidenceMapper.context(retrieved_chunks, max_context_chars)

    @staticmethod
    def _build_citations(
        retrieved_chunks: List[RetrievedChunkSchema],
    ) -> List[CitationSchema]:
        return LegacyEvidenceMapper.citations(retrieved_chunks)

    def retrieve(self, request: RAGToolInputSchema) -> RAGToolOutputSchema:
        started = self.timer.now()
        try:
            invocation = self.request_mapper.map(request)
            raw_result = self.backend.run(invocation.payload)
            latency_ms = elapsed_ms(self.timer, started)
            return self.result_mapper.map(
                request=request,
                invocation=invocation,
                raw_result=raw_result,
                latency_ms=latency_ms,
                rag_project_root=str(self.rag_project_root),
                skip_rerank=self.skip_rerank,
                service_name=self.service_name,
            )
        except Exception as exc:
            latency_ms = elapsed_ms(self.timer, started)
            return RAGToolOutputSchema(
                task_id=request.task_id,
                run_id=request.run_id,
                status="failed",
                query=request.query,
                retrieved_chunks=[],
                citations=[],
                trace=RAGTraceSchema(
                    retrieval_mode=request.retrieval_mode,
                    query=request.query,
                    latency_ms=latency_ms,
                    extra={
                        "rag_project_root": str(self.rag_project_root),
                        "rag_service": self.service_name,
                    },
                ),
                error=self.error_factory.create(
                    error_code="RAG_RETRIEVAL_FAILED",
                    error_type=exc.__class__.__name__,
                    message=str(exc),
                    recoverable=True,
                    retryable=True,
                    component=self.service_name,
                ),
                extra={
                    "source": "rag-template",
                    "rag_service": self.service_name,
                },
            )
