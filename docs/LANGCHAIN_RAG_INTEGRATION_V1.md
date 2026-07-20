# LangChain RAG Integration V1

## Goal

Expose the existing enterprise RAG boundary to LangChain without replacing the
retrieval architecture.

The canonical internal contract remains:

`RAGToolInputSchema -> RAGServicePort -> EvidenceBundleSchema`

LangChain adds two interoperability views:

1. `Runnable`: preserves the full enterprise contract.
2. `BaseRetriever`: projects selected evidence into `list[Document]`.

## Deliberately unchanged

- StaticRetrievalSpec
- RAGRequestPlanner
- Query Transform / Multi-Query / HyDE
- Dense + BM25 retrieval
- Source Fusion / Query Fusion
- Parent-child enrichment
- Reranking
- EvidenceAssessor
- CorrectiveRetrievalGate
- CorrectiveQueryPlanner
- ContextGate / ContextPack
- EvidenceBundle as the canonical RAG/Agent boundary

## Why the Retriever is not the canonical contract

LangChain Retriever is intentionally simple:

`str -> list[Document]`

The enterprise RAG boundary carries more semantics:

- selected vs dropped evidence
- citation bindings
- evidence assessment
- correction trace
- index/model lineage
- warnings and structured errors
- budget usage

Therefore `EvidenceBundleSchema` remains the source of truth. The Retriever is
an interoperability projection for LangChain chains and agents.

## Acceptance

1. Existing full regression suite remains green.
2. `test_langchain_rag_integration.py` passes.
3. Focused fake acceptance succeeds.
4. Focused real acceptance reaches the existing RAG runtime and returns a
   canonical EvidenceBundle through LangChain Runnable.
5. Direct RAG behavior and retrieval policies are unchanged.
