"""Stable outer ports for the standalone Agent-RAG V1 runtime."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    FactSnapshotSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1._base import ContractSchema
from apps.enterprise_document.services.agent_rag_v1 import AgentToolSession

from .contracts import (
    ExportedDocumentPathsSchema,
    MultiAgentProjectInputSchema,
    RetrievalRequestSchema,
    RetrievalResponseSchema,
    RuntimeTemplateSchema,
)


class AgentReasonerPort(Protocol):
    def invoke(
        self,
        *,
        context: AgentExecutionContextSchema,
        invocation_input: ContractSchema,
        tools: AgentToolSession,
    ) -> Mapping[str, Any] | ContractSchema:
        ...


class RetrievalServicePort(Protocol):
    def retrieve(
        self,
        request: RetrievalRequestSchema,
    ) -> RetrievalResponseSchema:
        ...


class FactSnapshotProviderPort(Protocol):
    def load(
        self,
        project_input: MultiAgentProjectInputSchema,
    ) -> FactSnapshotSchema:
        ...


class TemplateProviderPort(Protocol):
    def load(
        self,
        project_input: MultiAgentProjectInputSchema,
    ) -> RuntimeTemplateSchema:
        ...


class DocumentExporterPort(Protocol):
    def export(
        self,
        *,
        task_id: str,
        run_id: str,
        document_version: str,
        document_content: bytes,
        generation_report: Mapping[str, Any],
    ) -> ExportedDocumentPathsSchema:
        ...
