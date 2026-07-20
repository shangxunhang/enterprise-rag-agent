"""Composition root for the application-facing RAG service."""

from __future__ import annotations

from bootstrap.runtime_options import RuntimeOptions
from contracts.model_gateway import ModelGatewayPort
from contracts.observability import TraceSink
from contracts.rag import RAGServicePort
from rag.services.rag_service import FakeRAGService, ObservedRAGService, RAGService


class RAGServiceFactory:
    def build(
        self,
        options: RuntimeOptions,
        trace: TraceSink | None = None,
        *,
        model_gateway: ModelGatewayPort | None = None,
        model_name: str | None = None,
    ) -> RAGServicePort:
        if options.use_real_rag:
            inner: RAGServicePort = RAGService(
                rag_project_root=options.rag_project_root,
                static_retrieval_spec_file=options.rag_static_retrieval_spec_file,
                intent_policy_file=options.rag_intent_policy_file,
                retrieval_gate_policy_file=options.rag_retrieval_gate_policy_file,
                model_gateway=model_gateway,
                model_name=model_name,
            )
        else:
            inner = FakeRAGService()
        return ObservedRAGService(inner, trace)
