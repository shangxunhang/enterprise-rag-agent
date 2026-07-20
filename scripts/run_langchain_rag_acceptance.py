"""Focused acceptance for LangChain interoperability over the enterprise RAG service."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from rag.integrations.langchain import LangChainRAGRetriever, build_rag_runnable
from rag.services.rag_service import FakeRAGService, RAGService
from schemas.rag import RAGToolInputSchema


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--query",
        default="生成一个政务云建设方案",
        help="Query used for the focused LangChain RAG acceptance.",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="Use the real RAG service instead of the deterministic fake service.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    service = RAGService(PROJECT_ROOT) if args.real else FakeRAGService()

    request = RAGToolInputSchema(
        task_id="task_langchain_rag_acceptance",
        run_id="run_langchain_rag_acceptance",
        agent_name="LangChainRAGAcceptance",
        query=args.query,
        need_citation=True,
        max_context_chars=6000,
        max_context_items=3,
        extra={"retrieval_scope": "langchain_acceptance"},
    )

    runnable = build_rag_runnable(service)
    bundle = runnable.invoke(request)

    retriever = LangChainRAGRetriever(
        rag_service=service,
        request_template=request,
    )
    documents = retriever.invoke(args.query)

    summary = {
        "mode": "real" if args.real else "fake",
        "runnable": {
            "output_type": bundle.__class__.__name__,
            "status": bundle.status.value,
            "query": bundle.query,
            "selected_evidence_count": len(bundle.selected_evidence_ids),
            "dropped_evidence_count": len(bundle.dropped_evidence_ids),
            "citation_count": len(bundle.citations),
            "retrieval_trace_id": bundle.retrieval_trace_id,
        },
        "retriever": {
            "output_type": "list[Document]",
            "document_count": len(documents),
            "first_document_metadata": (
                documents[0].metadata if documents else {}
            ),
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if bundle.status.value == "failed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
