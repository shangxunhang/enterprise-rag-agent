"""Production ports used by mainline Section business Agents.

``MainlineResearchToolGateway`` is a Research-specific ``knowledge.retrieve``
binding adapter, not a second Tool authority. Compatibility invocations may still
delegate directly to ``SchemeEvidenceService``. Canonical M4 invocations require
an externally composed generic ``ToolGateway`` with canonical Budget/currentness/
authorization guards and a durable operational result store. The adapter itself
owns no ToolStore, Budget ledger, checkpoint, retry ledger, or Evidence truth.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
import hashlib
import json
from typing import Any, Callable, Iterator, Mapping

from agent.runtime.graph_state import GraphStateSchema
from contracts.evidence import evidence_bundle_sha256
from core.hashing import canonical_sha256
from core.runtime.side_effects import LogicalOperationRef
from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentRole,
    CanonicalEvidenceBundleBindingSchema,
    NormalizedToolResultSchema,
    ResearchRoundRefSchema,
    ToolHandlerOutputSchema,
    ToolProvenanceSchema,
    ToolQualitySchema,
    ToolResultErrorSchema,
    ToolResultStatus,
)
from apps.enterprise_document.schemas.pre_write_admission import (
    EvidenceRequirementsSnapshotSchema,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.agent_rag_v1.tool_gateway import ToolGateway
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
    canonical_bundle_binding: CanonicalEvidenceBundleBindingSchema | None = None
    tool_operation_refs: tuple[LogicalOperationRef, ...] = ()
    budget_receipt_refs: tuple[str, ...] = ()
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
    research_round_ref: ResearchRoundRefSchema | None = None
    canonical_evidence_requirements: EvidenceRequirementsSnapshotSchema | None = None
    canonical_evidence_requirement_ids: tuple[str, ...] = ()
    execution: MainlineResearchRetrieval | None = None


CanonicalToolGatewayProvider = Callable[[AgentExecutionContextSchema], ToolGateway]


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
            call_slot=self._call_count,
        )


class _UnboundResearchToolSession:
    """Precomputed document evidence path; no retrieval tool is authorized."""

    def __init__(self) -> None:
        self.call_refs: list[str] = []

    def call(self, tool_name: str, arguments: Mapping[str, Any]):
        del tool_name, arguments
        raise PermissionError("precomputed Research invocation has no tool access")


class MainlineResearchToolConfigurationError(RuntimeError):
    """A deterministic missing canonical dependency; retry cannot repair wiring."""


class MainlineResearchToolGateway:
    """Research-only gateway over the existing EvidenceService/RAGService."""

    tool_name = "knowledge.retrieve"
    tool_version = "mainline_evidence_service_v1"

    def __init__(
        self,
        *,
        evidence_service: SchemeEvidenceService,
        canonical_gateway_provider: CanonicalToolGatewayProvider | None = None,
    ) -> None:
        self.evidence_service = evidence_service
        self.canonical_gateway_provider = canonical_gateway_provider

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

    @staticmethod
    def _canonical_tool_operation_ref(
        *,
        round_ref: ResearchRoundRefSchema,
        tool_name: str,
        call_slot: int,
    ) -> LogicalOperationRef:
        slot = int(call_slot)
        if slot < 1:
            raise ValueError("canonical Research tool call slot must be positive")
        operation_id = "research-tool:" + canonical_sha256(
            {
                "contract": "research_tool_operation_v1",
                "research_round_ref": round_ref.model_dump(
                    mode="json",
                    round_trip=True,
                ),
                "tool_name": tool_name,
                "call_slot": slot,
            }
        )
        return LogicalOperationRef(
            logical_operation_id=operation_id,
            operation_kind="TOOL_INVOCATION",
            run_id=round_ref.run_id,
            operation_scope_ref=(
                f"research-round:{round_ref.research_round_id}:"
                f"tool:{tool_name}:slot:{slot}"
            ),
            attempt_scope_ref=round_ref.attempt_scope_ref,
            attempt_id=round_ref.attempt_id,
        )

    def _restore_execution_from_canonical_result(
        self,
        request: BoundResearchToolRequest,
        result: NormalizedToolResultSchema,
    ) -> MainlineResearchRetrieval:
        data = dict(result.data)
        if not bool(data.get("retrieval_succeeded")):
            return MainlineResearchRetrieval(
                success=False,
                evidence_bundle=None,
                context=None,
                chunks=(),
                citations=(),
                citation_registry=tuple(request.citation_registry),
                normalized={},
                assessment={"status": "not_assessed", "details": {}},
                retrieval_trace_id=None,
                error_message=str(
                    data.get("error_message")
                    or result.error.error_code
                    or "retrieval_failed"
                ),
            )

        handle = str(data.get("evidence_bundle_handle") or "").strip()
        if not handle:
            raise RuntimeError("canonical Tool replay lacks EvidenceBundle handle")
        bundle = self.evidence_service.resolve_canonical_bundle(handle)
        binding = self.evidence_service.canonical_bundle_binding(bundle)
        declared_hash = str(data.get("evidence_contract_sha256") or "").strip()
        if declared_hash and declared_hash != evidence_bundle_sha256(bundle):
            raise RuntimeError("canonical Tool replay EvidenceBundle hash mismatch")
        if binding.evidence_bundle_handle != handle:
            raise RuntimeError(
                "canonical Tool replay resolved a different Bundle handle"
            )
        context_value, chunks, citations, normalized = (
            self.evidence_service.extract_rag_output(
                request.shared_state,
                bundle,
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
        return MainlineResearchRetrieval(
            success=True,
            evidence_bundle=bundle,
            context=context_value,
            chunks=tuple(chunks),
            citations=tuple(citations),
            citation_registry=tuple(registry.all()),
            normalized=dict(normalized),
            assessment=_assessment(bundle),
            retrieval_trace_id=(
                str(bundle.retrieval_trace_id).strip()
                if bundle.retrieval_trace_id
                else None
            ),
            canonical_bundle_binding=binding,
        )

    def _invoke_canonical(
        self,
        context: AgentExecutionContextSchema,
        request: BoundResearchToolRequest,
        *,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        call_slot: int,
    ) -> NormalizedToolResultSchema:
        if self.canonical_gateway_provider is None:
            raise MainlineResearchToolConfigurationError("canonical ToolGateway provider is not configured")
        round_ref = request.research_round_ref
        if round_ref is None:
            raise RuntimeError(
                "canonical Research Tool execution requires ResearchRoundRef"
            )
        if (
            round_ref.run_id != context.run_id
            or round_ref.section_id != request.section_id
            or round_ref.attempt_id != context.attempt_id
        ):
            raise RuntimeError(
                "canonical Research Tool context does not match ResearchRoundRef"
            )
        operation_ref = self._canonical_tool_operation_ref(
            round_ref=round_ref,
            tool_name=tool_name,
            call_slot=call_slot,
        )

        def _handler(invocation) -> ToolHandlerOutputSchema:
            legacy = self._invoke_legacy_bound(
                context,
                request,
                call_id=invocation.call_id,
                tool_name=invocation.tool_name,
                arguments=invocation.arguments,
            )
            return ToolHandlerOutputSchema(
                status=legacy.status,
                data_type=legacy.data_type,
                data=dict(legacy.data),
                source_refs=list(legacy.provenance.source_refs),
                warnings=list(legacy.quality.warnings),
                error_code=legacy.error.error_code,
            )

        # Provider returns one invocation-local generic gateway over durable
        # owners; request-specific binding registration remains here.
        gateway = self.canonical_gateway_provider(context)
        if gateway.budget_runtime is None:
            raise MainlineResearchToolConfigurationError(
                "canonical Research ToolGateway requires canonical Budget runtime"
            )
        gateway.register(
            name=self.tool_name,
            version=self.tool_version,
            handler=_handler,
            max_retries=0,
        )
        result = gateway.invoke(
            context,
            call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
            tool_operation_id=operation_ref.logical_operation_id,
        )
        execution = request.execution
        if execution is None:
            execution = self._restore_execution_from_canonical_result(
                request,
                result,
            )
        budget_refs = (
            (result.budget_settlement.reservation_id,)
            if result.budget_settlement is not None
            else ()
        )
        request.execution = replace(
            execution,
            tool_operation_refs=(operation_ref,),
            budget_receipt_refs=budget_refs,
        )
        return result

    def invoke_bound(
        self,
        context: AgentExecutionContextSchema,
        request: BoundResearchToolRequest,
        *,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        call_slot: int = 1,
    ) -> NormalizedToolResultSchema:
        if request.research_round_ref is not None:
            return self._invoke_canonical(
                context,
                request,
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
                call_slot=call_slot,
            )
        return self._invoke_legacy_bound(
            context,
            request,
            call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
        )

    def _invoke_legacy_bound(
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
                canonical_evidence_requirements=(
                    request.canonical_evidence_requirements
                ),
                canonical_evidence_requirement_ids=(
                    request.canonical_evidence_requirement_ids
                ),
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
            binder = getattr(
                self.evidence_service,
                "canonical_bundle_binding",
                None,
            )
            canonical_bundle_binding = binder(result) if callable(binder) else None
            accepted_ids = list(result.selected_evidence_ids)
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
                canonical_bundle_binding=canonical_bundle_binding,
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
                    "accepted_evidence_ids": list(accepted_ids),
                    "rejected_evidence_ids": [],
                    "tool_call_ids": (
                        [retrieval_trace_id] if retrieval_trace_id else []
                    ),
                    "retrieval_scope": request.scope,
                    "citation_count": len(citations),
                    "retrieved_chunk_count": len(chunks),
                    "evidence_contract_sha256": evidence_contract_sha256,
                    "evidence_bundle_handle": (
                        canonical_bundle_binding.evidence_bundle_handle
                        if canonical_bundle_binding is not None
                        else None
                    ),
                },
                provenance=provenance,
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
