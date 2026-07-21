"""Application-facing RAG services that return canonical evidence bundles."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contracts.model_gateway import ModelGatewayPort
from contracts.observability import TraceSink
from contracts.rag import RAGServicePort
from core.error_factory import ErrorFactory
from core.runtime.timing import MonotonicTimer, Timer, elapsed_ms
from observability.trace_context import activate_span, current_span, new_span
from rag.mapping.request_mapper import RAGRequestMapper
from rag.mapping.result_mapper import RAGResultMapper
from rag.runtime.parent_child_runtime_factory import ParentChildRuntimeFactory
from rag.runtime.retrieval_runtime import RetrievalRuntime, RetrievalRuntimeConfig
from schemas.rag import (
    EvidenceBundleSchema,
    RAGContextSchema,
    RAGToolInputSchema,
    RAGToolOutputSchema,
    RAGTraceSchema,
)
from schemas.status import ExecutionStatus
from schemas.tool import ToolCallSchema
from tools.fake_rag_tool import FakeRAGTool


def _bundle_from_tool_output(
    request: RAGToolInputSchema,
    output: RAGToolOutputSchema,
) -> EvidenceBundleSchema:
    bundle = output.evidence
    if bundle is None:
        context = output.context or RAGContextSchema(
            context_text="",
            max_context_chars=int(request.max_context_chars or 6000),
            used_context_chars=0,
            context_item_count=0,
        )
        bundle = EvidenceBundleSchema(
            query=request.query,
            rewritten_queries=list(output.rewritten_queries),
            context=context,
        )

    trace_id = str(
        request.extra.get("retrieval_trace_id")
        or f"rag_{request.run_id}_{request.extra.get('retrieval_scope') or 'document'}"
    )
    evidence_quality = (
        output.extra.get("evidence_quality")
        if isinstance(output.extra, dict)
        else None
    )
    corrective = (
        evidence_quality.get("corrective_retrieval")
        if isinstance(evidence_quality, dict)
        else None
    )
    rounds = corrective.get("rounds") if isinstance(corrective, dict) else []
    correction_trace = [
        dict(item) for item in rounds if isinstance(item, dict)
    ] if isinstance(rounds, list) else []
    return bundle.model_copy(
        update={
            "task_id": request.task_id,
            "run_id": request.run_id,
            "status": output.status,
            "retrieval_trace_id": trace_id,
            "correction_trace": correction_trace,
            "budget_usage": {
                "retrieval_rounds": 1 + len(correction_trace),
                "queries_executed": 1 + len(bundle.rewritten_queries),
                "rerank_calls": int(bool(output.trace and output.trace.reranked_count)),
            },
            "trace": output.trace,
            "warnings": list(output.warnings),
            "error": output.error,
            "extra": {
                **dict(bundle.extra),
                "provider_output": {
                    "schema_version": output.schema_version,
                    "extra": dict(output.extra),
                },
            },
        }
    )


class RAGService(RAGServicePort):
    """Run configured retrieval and expose exactly one public response type."""

    service_name = "RAGService"

    def __init__(
        self,
        rag_project_root: str | Path,
        *,
        static_retrieval_spec_file: str | Path | None = None,
        intent_policy_file: str | Path | None = None,
        retrieval_gate_policy_file: str | Path | None = None,
        model_gateway: ModelGatewayPort | None = None,
        model_name: str | None = None,
        retrieval_runtime: Any | None = None,
        request_mapper: RAGRequestMapper | None = None,
        allow_legacy_unscoped: bool = False,
        result_mapper: RAGResultMapper | None = None,
        error_factory: ErrorFactory | None = None,
        timer: Timer | None = None,
    ) -> None:
        self.rag_project_root = Path(rag_project_root).resolve()
        static_spec_file = Path(
            static_retrieval_spec_file
            or "backend/rag/config/static_retrieval_v1.yaml"
        ).expanduser()
        if not static_spec_file.is_absolute():
            static_spec_file = self.rag_project_root / static_spec_file
        intent_file = Path(
            intent_policy_file or "backend/rag/config/intent_policy_v1.yaml"
        ).expanduser()
        if not intent_file.is_absolute():
            intent_file = self.rag_project_root / intent_file
        gate_file = Path(
            retrieval_gate_policy_file
            or "backend/rag/config/retrieval_gate_policy_v1.yaml"
        ).expanduser()
        if not gate_file.is_absolute():
            gate_file = self.rag_project_root / gate_file
        self.retrieval_runtime = retrieval_runtime or RetrievalRuntime(
            RetrievalRuntimeConfig(
                parent_file="data/processed/parent_child_chunks/parent_chunks.jsonl",
                child_file="data/processed/parent_child_chunks/child_chunks.jsonl",
                db_file="data/processed/vector_store/milvus_parent_child.db",
                capture_output="data/processed/runs/retrieval_runs.jsonl",
                static_retrieval_spec_file=str(static_spec_file.resolve()),
                intent_policy_file=str(intent_file.resolve()),
                retrieval_gate_policy_file=str(gate_file.resolve()),
            ),
            project_root=self.rag_project_root,
            runtime_factory=ParentChildRuntimeFactory(
                model_gateway=model_gateway,
                model_name=model_name,
            ),
        )
        self.request_mapper = request_mapper or RAGRequestMapper(
            allow_legacy_unscoped=allow_legacy_unscoped
        )
        self.result_mapper = result_mapper or RAGResultMapper()
        self.error_factory = error_factory or ErrorFactory()
        self.timer = timer or MonotonicTimer()

    def retrieve(self, request: RAGToolInputSchema) -> EvidenceBundleSchema:
        started = self.timer.now()
        try:
            invocation = self.request_mapper.map(request)
            raw_result = self.retrieval_runtime.retrieve(invocation.payload)
            return self.result_mapper.map(
                request=request,
                invocation=invocation,
                raw_result=raw_result,
                latency_ms=elapsed_ms(self.timer, started),
                rag_project_root=str(self.rag_project_root),
                service_name=self.service_name,
            )
        except Exception as exc:
            trace = RAGTraceSchema(
                retrieval_mode="adaptive",
                query=request.query,
                latency_ms=elapsed_ms(self.timer, started),
            )
            return EvidenceBundleSchema(
                task_id=request.task_id,
                run_id=request.run_id,
                status=ExecutionStatus.FAILED,
                query=request.query,
                retrieval_trace_id=str(
                    request.extra.get("retrieval_trace_id")
                    or f"rag_{request.run_id}_{request.extra.get('retrieval_scope') or 'document'}"
                ),
                context=RAGContextSchema(
                    context_text="",
                    max_context_chars=int(request.max_context_chars or 6000),
                    used_context_chars=0,
                    context_item_count=0,
                ),
                trace=trace,
                error=self.error_factory.create(
                    error_code="RAG_RETRIEVAL_FAILED",
                    error_type=exc.__class__.__name__,
                    message=str(exc),
                    recoverable=True,
                    retryable=True,
                    component=self.service_name,
                ),
            )

    def close(self) -> None:
        close = getattr(self.retrieval_runtime, "close", None)
        if callable(close):
            close()


class FakeRAGService(RAGServicePort):
    """Deterministic evidence service used by the fake mainline."""

    def __init__(self, tool: FakeRAGTool | None = None) -> None:
        self.tool = tool or FakeRAGTool()

    def retrieve(self, request: RAGToolInputSchema) -> EvidenceBundleSchema:
        tool_call_id = str(
            request.extra.get("retrieval_trace_id")
            or f"rag_{request.run_id}_{request.extra.get('retrieval_scope') or 'document'}"
        )
        result = self.tool.run(
            ToolCallSchema(
                tool_call_id=tool_call_id,
                task_id=request.task_id,
                run_id=request.run_id,
                tool_name=self.tool.tool_name,
                tool_input={**request.model_dump(), **dict(request.extra)},
                caller_agent=request.agent_name,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        return _bundle_from_tool_output(
            request,
            RAGToolOutputSchema.model_validate(result.result or {}),
        )


class ObservedRAGService(RAGServicePort):
    """Trace decorator for direct deterministic RAG calls."""

    def __init__(self, inner: RAGServicePort, trace: TraceSink | None) -> None:
        self.inner = inner
        self.trace = trace

    def _record(self, **kwargs: Any) -> None:
        if self.trace is not None:
            self.trace.record(**kwargs)

    def retrieve(self, request: RAGToolInputSchema) -> EvidenceBundleSchema:
        span = new_span(
            run_id=request.run_id,
            span_name="rag:retrieve",
            span_kind="client",
            parent=current_span(),
        )
        call_id = str(
            request.extra.get("retrieval_trace_id") or f"rag_{request.run_id}"
        )
        self._record(
            task_id=request.task_id,
            run_id=request.run_id,
            event_type="rag_started",
            component_type="rag",
            component_name=self.inner.__class__.__name__,
            call_id=call_id,
            caller=request.agent_name,
            callee="RAGService",
            status=ExecutionStatus.RUNNING.value,
            phase="start",
            trace_id=span.trace_id,
            span_id=span.span_id,
            parent_span_id=span.parent_span_id,
            span_name=span.span_name,
            span_kind=span.span_kind,
            started_at=span.started_at,
            input_summary={
                "query": request.query,
                "scope": request.extra.get("retrieval_scope"),
            },
            tags=["trace_v2", "rag"],
        )
        with activate_span(span):
            bundle = self.inner.retrieve(request)
        self._record(
            task_id=request.task_id,
            run_id=request.run_id,
            event_type="rag_finished",
            component_type="rag",
            component_name=self.inner.__class__.__name__,
            call_id=call_id,
            caller=request.agent_name,
            callee="RAGService",
            status=bundle.status.value,
            error_message=bundle.error_message,
            latency_ms=span.latency_ms(),
            phase="error" if bundle.error else "end",
            trace_id=span.trace_id,
            span_id=span.span_id,
            parent_span_id=span.parent_span_id,
            span_name=span.span_name,
            span_kind=span.span_kind,
            started_at=span.started_at,
            output_summary={
                "schema_version": bundle.schema_version,
                "selected_evidence_count": len(bundle.selected_evidence_ids),
                "citation_count": len(bundle.citations),
                "retrieval_trace_id": bundle.retrieval_trace_id,
            },
            lineage=bundle.lineage.model_dump(),
            tags=["trace_v2", "rag"],
        )
        return bundle

    def close(self) -> None:
        """Forward lifecycle shutdown to the wrapped RAG service."""
        close = getattr(self.inner, "close", None)
        if callable(close):
            close()
