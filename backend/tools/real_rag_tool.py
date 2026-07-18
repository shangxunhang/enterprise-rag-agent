"""Agent-facing adapter for the shared RAG services contract."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict

from contracts.rag import BaseRAGService
from contracts.base_tool import BaseTool
from schemas.common import ErrorSchema, ErrorSourceSchema
from schemas.rag import RAGToolInputSchema
from schemas.status import ExecutionStatus
from schemas.tool import ToolCallSchema, ToolResultSchema


class RealRAGTool(BaseTool):
    """Translate Agent tool calls into public RAG services requests."""

    tool_name = "RealRAGTool"
    description = "调用已注入的 RAG Service 执行真实知识检索。"

    def __init__(self, rag_service: BaseRAGService) -> None:
        self.rag_service = rag_service

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        if value is None or value == "":
            return default
        return int(value)

    @staticmethod
    def _as_bool(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _build_request(self, tool_call: ToolCallSchema) -> RAGToolInputSchema:
        """Convert the generic ToolCall payload into the public RAG request."""

        tool_input: Dict[str, Any] = dict(tool_call.tool_input or {})
        query = str(tool_input.get("query") or "").strip()
        if not query:
            raise ValueError("RealRAGTool requires tool_input['query'].")

        retrieval_mode = str(
            tool_input.get("retrieval_mode")
            or tool_input.get("retrieval_strategy")
            or "hybrid"
        ).strip() or "hybrid"

        # ``extra`` deliberately retains the complete original Tool payload.
        # Adapter-specific options such as enable_crag / enable_hyde are not
        # part of the stable public schema yet, but LegacyRAGService still needs
        # them during the migration period.
        return RAGToolInputSchema(
            task_id=tool_call.task_id,
            run_id=tool_call.run_id,
            agent_name=tool_call.caller_agent or "unknown_agent",
            query=query,
            rewritten_queries=tool_input.get("rewritten_queries") or [],
            kb_ids=tool_input.get("kb_ids") or [],
            retrieval_mode=retrieval_mode,
            top_k=self._as_int(tool_input.get("top_k"), 10),
            dense_top_k=self._as_int(tool_input.get("dense_top_k"), 10),
            keyword_top_k=self._as_int(
                tool_input.get("keyword_top_k"),
                10,
            ),
            candidate_top_k=self._as_int(
                tool_input.get("candidate_top_k"),
                10,
            ),
            rerank_top_k=self._as_int(
                tool_input.get("rerank_top_k"),
                5,
            ),
            filters=tool_input.get("filters") or {},
            need_context=self._as_bool(
                tool_input.get("need_context"),
                True,
            ),
            need_citation=self._as_bool(
                tool_input.get("need_citation"),
                True,
            ),
            max_context_chars=self._as_int(
                tool_input.get("max_context_chars"),
                6000,
            ),
            max_context_items=self._as_int(
                tool_input.get("max_context_items"),
                3,
            ),
            score_threshold=tool_input.get("score_threshold"),
            mode=str(tool_input.get("mode") or "retrieve_only"),
            extra=tool_input,
        )

    def run(self, tool_call: ToolCallSchema) -> ToolResultSchema:
        """Execute the injected RAG services and wrap its result as a ToolResult."""

        started = time.time()
        created_at = self._now_iso()

        try:
            request = self._build_request(tool_call)
            rag_result = self.rag_service.retrieve(request)
            latency_ms = int((time.time() - started) * 1000)
            success = rag_result.status in {
                ExecutionStatus.SUCCESS,
                ExecutionStatus.PARTIAL_SUCCESS,
            }

            return ToolResultSchema(
                tool_call_id=tool_call.tool_call_id,
                task_id=tool_call.task_id,
                run_id=tool_call.run_id,
                tool_name=self.tool_name,
                success=success,
                result=rag_result.model_dump(),
                error=rag_result.error,
                error_message=(
                    rag_result.error.message
                    if rag_result.error is not None
                    else None
                ),
                started_at=created_at,
                finished_at=self._now_iso(),
                latency_ms=latency_ms,
                created_at=created_at,
                metadata={
                    "output_schema": "RAGToolOutputSchema",
                    "output_schema_version": rag_result.schema_version,
                    "rag_service": self.rag_service.__class__.__name__,
                },
                extra={
                    "adapter": self.__class__.__name__,
                    "rag_service": self.rag_service.__class__.__name__,
                },
            )

        except Exception as exc:
            latency_ms = int((time.time() - started) * 1000)
            error = ErrorSchema(
                error_code="RAG_TOOL_ADAPTER_FAILED",
                error_type=exc.__class__.__name__,
                message=str(exc),
                recoverable=True,
                retryable=False,
                source=ErrorSourceSchema(
                    component=self.__class__.__name__,
                    tool_name=self.tool_name,
                ),
            )

            return ToolResultSchema(
                tool_call_id=tool_call.tool_call_id,
                task_id=tool_call.task_id,
                run_id=tool_call.run_id,
                tool_name=self.tool_name,
                success=False,
                result={},
                error=error,
                error_message=f"{exc.__class__.__name__}: {exc}",
                started_at=created_at,
                finished_at=self._now_iso(),
                latency_ms=latency_ms,
                created_at=created_at,
                metadata={
                    "rag_service": self.rag_service.__class__.__name__,
                },
                extra={
                    "adapter": self.__class__.__name__,
                },
            )
