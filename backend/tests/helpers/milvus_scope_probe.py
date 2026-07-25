"""Run one real Milvus Lite authorization-filter probe in an isolated process."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
from pymilvus import MilvusClient

from rag.mapping.request_mapper import RAGRequestMapper
from rag.retriever.milvus_child_retriever import MilvusChildRetriever
from rag.vector_store.milvus_child_chunk_store import (
    build_milvus_child_chunk_record,
    create_or_reset_child_chunk_collection,
    insert_child_chunk_records,
)
from schemas.rag import RAGToolInputSchema, RetrievalAccessScopeSchema


def main(root: Path) -> None:
    db_path = root / "authorization_scope.db"
    collection = "scope_rows"
    client = MilvusClient(str(db_path))
    try:
        create_or_reset_child_chunk_collection(
            client,
            collection_name=collection,
            dim=4,
            metric_type="COSINE",
            recreate=True,
            max_text_chars=1024,
        )
        records = [
            build_milvus_child_chunk_record(
                {
                    "chunk_id": "childA",
                    "child_chunk_id": "childA",
                    "parent_chunk_id": "parentA",
                    "tenant_id": "tenantA",
                    "kb_id": "kbA",
                    "file_id": "fileA",
                    "doc_id": "docA",
                    "text": "authorized row",
                },
                np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
                embedding_model="test",
                embedding_dim=4,
                embedding_version="v1",
                max_text_chars=1024,
            ),
            build_milvus_child_chunk_record(
                {
                    "chunk_id": "childB",
                    "child_chunk_id": "childB",
                    "parent_chunk_id": "parentB",
                    "tenant_id": "tenantB",
                    "kb_id": "kbB",
                    "file_id": "fileB",
                    "doc_id": "docB",
                    "text": "unauthorized row",
                },
                np.asarray([1.0, 0.01, 0.0, 0.0], dtype=np.float32),
                embedding_model="test",
                embedding_dim=4,
                embedding_version="v1",
                max_text_chars=1024,
            ),
        ]
        insert_child_chunk_records(client, collection, records, batch_size=2)
    finally:
        client.close()

    invocation = RAGRequestMapper().map(
        RAGToolInputSchema(
            task_id="task",
            run_id="run",
            agent_name="agent",
            query="scope",
            kb_ids=["kbA", "kbB"],
            access_scope=RetrievalAccessScopeSchema(
                tenant_id="tenantA",
                authorized_kb_ids=["kbA"],
            ),
        )
    )
    retriever = MilvusChildRetriever(
        db_path,
        collection_name=collection,
        metric_type="COSINE",
        hash_embedding=True,
        hash_dim=4,
    )
    try:
        hits = retriever.search_by_vector(
            np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
            top_k=10,
            filter_expr=invocation.payload["filter_expr"],
        )
    finally:
        retriever.close()

    rows = [[item["tenant_id"], item["kb_id"]] for item in hits]
    print(
        "SCOPE_RESULT="
        + json.dumps(
            {
                "rows": rows,
                "tenantB_rows": sum(
                    item["tenant_id"] == "tenantB" for item in hits
                ),
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
