"""Production ports used by mainline Section business Agents.

The Research gateway is intentionally ephemeral. It does not own a ToolStore,
ArtifactStore, checkpoint, retry ledger or task state. One invocation binds one
request through a ``ContextVar``; the tool session delegates to the existing
``SchemeEvidenceService`` and therefore to the already composed RAGService and
Milvus runtime.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterator, Mapping

from agent.runtime.graph_state import GraphStateSchema
from rag.evidence.contract import evidence_bundle_sha256
from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentRole,
    NormalizedToolResultSchema,
    ToolProvenanceSchema,
    ToolQualitySchema,
    ToolResultErrorSchema,
    ToolResultStatus,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.scheme_writer.document_citation_registry import (
    DocumentCitationRegistry,
)
from apps.enterprise_document.services.scheme_writer.evidence_service import (
    SchemeEvidenceService,
)
from schemas.citation import CitationSchema
from schemas.rag import EvidenceBundleSchema, RAGContextSchema, RetrievedChunkSchema


@dataclass(frozen=True)
class MainlineResearchRetrieval:
    """Canonical retrieval data returned to the owning Section graph node."""

    success: bool
    evidence_bundle: EvidenceBundleSchema | None
    context: RAGContextSchema | None
    chunks: tuple[RetrievedChunkSchema, ...]
    citations: tuple[CitationSchema, ...]
    citation_registry: tuple[CitationSchema, ...]
    normalized: Mapping[str, Any]
    assessment: Mapping[str, Any]
    retrieval_trace_id: str | None
    error_message: str | None = None


@dataclass
class BoundResearchToolRequest:
    """Live dependencies for exactly one ResearchAgent invocation."""

    shared_state: GraphStateSchema
    project_input: ProjectInputSchema
    section_id: str
    section_title: str
    query: str
    scope: str
    call_suffix: str
    citation_registry: list[CitationSchema]
    execution: MainlineResearchRetrieval | None = None


_ACTIVE_RESEARCH_REQUEST: ContextVar[BoundResearchToolRequest | None] = ContextVar(
    "mainline_research_tool_request",
    default=None,
)


def _assessment(bundle: EvidenceBundleSchema) -> dict[str, Any]:
    """Project assessment from the typed canonical Bundle, never from normalized dict."""

    value = bundle.assessment.model_dump(mode="json", exclude_none=True)
    raw_status = value.get("status") or "not_assessed"
    if hasattr(raw_status, "value"):
        raw_status = raw_status.value
    value["status"] = str(raw_status).strip().lower() or "not_assessed"
    details = value.get("details")
    value["details"] = dict(details) if isinstance(details, Mapping) else {}
    return value


class MainlineResearchToolSession:
    """One-invocation ToolGateway session with no independent persistence."""

    def __init__(
        self,
        *,
        gateway: "MainlineResearchToolGateway",
        context: AgentExecutionContextSchema,
        request: BoundResearchToolRequest,
    ) -> None:
        self.gateway = gateway
        self.context = context
        self.request = request
        self.call_refs: list[str] = []
        self._call_count = 0

    def call(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> NormalizedToolResultSchema:
        self._call_count += 1
        call_id = (
            f"{self.context.invocation_id}:tool:{self._call_count:03d}:"
            f"{tool_name}"
        )
        self.call_refs.append(call_id)
        return self.gateway.invoke_bound(
            self.context,
            self.request,
            call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
        )


class _UnboundResearchToolSession:
    """Precomputed document evidence path; no retrieval tool is authorized."""

    def __init__(self) -> None:
        self.call_refs: list[str] = []

    def call(self, tool_name: str, arguments: Mapping[str, Any]):
        del tool_name, arguments
        raise PermissionError("precomputed Research invocation has no tool access")


class MainlineResearchToolGateway:
    """Research-only gateway over the existing EvidenceService/RAGService."""

    tool_name = "knowledge.retrieve"
    tool_version = "mainline_evidence_service_v1"

    def __init__(self, *, evidence_service: SchemeEvidenceService) -> None:
        self.evidence_service = evidence_service

    @contextmanager
    def bind(
        self,
        request: BoundResearchToolRequest,
    ) -> Iterator[BoundResearchToolRequest]:
        token = _ACTIVE_RESEARCH_REQUEST.set(request)
        try:
            yield request
        finally:
            _ACTIVE_RESEARCH_REQUEST.reset(token)

    def session(
        self,
        context: AgentExecutionContextSchema,
    ) -> MainlineResearchToolSession | _UnboundResearchToolSession:
        request = _ACTIVE_RESEARCH_REQUEST.get()
        if request is None and not context.authorized_tool_names:
            return _UnboundResearchToolSession()
        if request is None:
            raise RuntimeError(
                "ResearchAgent tool session requires an active mainline request"
            )
        if context.agent_role != AgentRole.RESEARCH:
            raise PermissionError("mainline research gateway accepts ResearchAgent only")
        if self.tool_name not in context.authorized_tool_names:
            raise PermissionError("ResearchAgent is not authorized for knowledge.retrieve")
        if context.section_id != request.section_id:
            raise ValueError("Research tool request section does not match Agent context")
        return MainlineResearchToolSession(
            gateway=self,
            context=context,
            request=request,
        )

    @staticmethod
    def _fingerprint(
        *,
        context: AgentExecutionContextSchema,
        arguments: Mapping[str, Any],
    ) -> str:
        raw = json.dumps(
            {
                "input_snapshot_version": context.input_snapshot_version,
                "arguments": dict(arguments),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def invoke_bound(
        self,
        context: AgentExecutionContextSchema,
        request: BoundResearchToolRequest,
        *,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> NormalizedToolResultSchema:
        fingerprint = self._fingerprint(context=context, arguments=arguments)
        provenance = ToolProvenanceSchema(
            input_snapshot_version=context.input_snapshot_version,
            tool_name=tool_name,
            tool_version=self.tool_version,
            input_fingerprint=fingerprint,
        )
        if tool_name != self.tool_name:
            return NormalizedToolResultSchema(
                call_id=call_id,
                status=ToolResultStatus.REJECTED,
                data_type="research_retrieval",
                provenance=provenance,
                error=ToolResultErrorSchema(error_code="TOOL_PERMISSION_DENIED"),
            )

        query = str(arguments.get("query") or "").strip()
        scope = str(arguments.get("scope") or "").strip()
        section_id = str(arguments.get("section_id") or "").strip()
        if (
            query != request.query
            or scope != request.scope
            or section_id != request.section_id
        ):
            return NormalizedToolResultSchema(
                call_id=call_id,
                status=ToolResultStatus.REJECTED,
                data_type="research_retrieval",
                provenance=provenance,
                error=ToolResultErrorSchema(error_code="TOOL_ARGUMENT_MISMATCH"),
            )

        try:
            result = self.evidence_service.retrieve(
                request.shared_state,
                request.project_input,
                query=request.query,
                scope=request.scope,
                section_id=request.section_id,
                section_title=request.section_title,
                call_suffix=request.call_suffix,
            )
            if result is None:
                execution = MainlineResearchRetrieval(
                    success=False,
                    evidence_bundle=None,
                    context=None,
                    chunks=(),
                    citations=(),
                    citation_registry=tuple(request.citation_registry),
                    normalized={},
                    assessment={"status": "not_assessed", "details": {}},
                    retrieval_trace_id=None,
                    error_message="rag_service_not_configured",
                )
                request.execution = execution
                return NormalizedToolResultSchema(
                    call_id=call_id,
                    status=ToolResultStatus.EMPTY,
                    data_type="research_retrieval",
                    data={
                        "retrieval_succeeded": False,
                        "assessment": dict(execution.assessment),
                        "accepted_evidence_ids": [],
                        "rejected_evidence_ids": [],
                        "tool_call_ids": [],
                        "retrieval_scope": request.scope,
                        "error_message": execution.error_message,
                    },
                    provenance=provenance,
                )

            retrieval_trace_id = str(
                getattr(result, "retrieval_trace_id", "") or ""
            ).strip() or None
            if not bool(result.success):
                error_message = str(
                    getattr(result, "error_message", "") or "retrieval_failed"
                )
                execution = MainlineResearchRetrieval(
                    success=False,
                    evidence_bundle=None,
                    context=None,
                    chunks=(),
                    citations=(),
                    citation_registry=tuple(request.citation_registry),
                    normalized={},
                    assessment={"status": "not_assessed", "details": {}},
                    retrieval_trace_id=retrieval_trace_id,
                    error_message=error_message,
                )
                request.execution = execution
                return NormalizedToolResultSchema(
                    call_id=call_id,
                    status=ToolResultStatus.PARTIAL,
                    data_type="research_retrieval",
                    data={
                        "retrieval_succeeded": False,
                        "assessment": dict(execution.assessment),
                        "accepted_evidence_ids": [],
                        "rejected_evidence_ids": [],
                        "tool_call_ids": (
                            [retrieval_trace_id] if retrieval_trace_id else []
                        ),
                        "retrieval_scope": request.scope,
                        "error_message": error_message,
                    },
                    provenance=provenance,
                    quality=ToolQualitySchema(warnings=[error_message]),
                )

            context_value, chunks, citations, normalized = (
                self.evidence_service.extract_rag_output(
                    request.shared_state,
                    result,
                )
            )
            registry = DocumentCitationRegistry.from_citations(
                request.citation_registry
            )
            context_value, chunks, citations, normalized = registry.remap_bundle(
                context=context_value,
                chunks=chunks,
                citations=citations,
                normalized=normalized,
                scope=request.scope,
                query=request.query,
            )
            assessment = _assessment(result)
            evidence_contract_sha256 = evidence_bundle_sha256(result)
            accepted_ids = [
                str(item.citation_id).strip()
                for item in citations
                if str(item.citation_id).strip()
            ]
            if not accepted_ids:
                accepted_ids = [
                    str(getattr(item, "chunk_id", "") or "").strip()
                    for item in chunks
                    if str(getattr(item, "chunk_id", "") or "").strip()
                ]
            execution = MainlineResearchRetrieval(
                success=True,
                evidence_bundle=result,
                context=context_value,
                chunks=tuple(chunks),
                citations=tuple(citations),
                citation_registry=tuple(registry.all()),
                normalized=dict(normalized),
                assessment=assessment,
                retrieval_trace_id=retrieval_trace_id,
            )
            request.execution = execution
            return NormalizedToolResultSchema(
                call_id=call_id,
                status=(
                    ToolResultStatus.SUCCESS
                    if accepted_ids or context_value.context_text.strip()
                    else ToolResultStatus.EMPTY
                ),
                data_type="research_retrieval",
                data={
                    "retrieval_succeeded": True,
                    "assessment": dict(assessment),
                    "accepted_evidence_ids": list(dict.fromkeys(accepted_ids)),
                    "rejected_evidence_ids": [],
                    "tool_call_ids": (
                        [retrieval_trace_id] if retrieval_trace_id else []
                    ),
                    "retrieval_scope": request.scope,
                    "citation_count": len(citations),
                    "retrieved_chunk_count": len(chunks),
                    "evidence_contract_sha256": evidence_contract_sha256,
                },
                provenance=provenance.model_copy(
                    update={"source_refs": list(dict.fromkeys(accepted_ids))},
                    deep=True,
                ),
            )
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"
            request.execution = MainlineResearchRetrieval(
                success=False,
                evidence_bundle=None,
                context=None,
                chunks=(),
                citations=(),
                citation_registry=tuple(request.citation_registry),
                normalized={},
                assessment={"status": "not_assessed", "details": {}},
                retrieval_trace_id=None,
                error_message=error_message,
            )
            return NormalizedToolResultSchema(
                call_id=call_id,
                status=ToolResultStatus.RETRYABLE_ERROR,
                data_type="research_retrieval",
                data={"retrieval_succeeded": False},
                provenance=provenance,
                error=ToolResultErrorSchema(error_code=type(exc).__name__),
                quality=ToolQualitySchema(warnings=[error_message]),
            )
