"""Regression tests for real parent-store loading in the RAG runtime."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from rag.runtime.resource_pool import ParentChildResourcePool
from rag.runtime.retrieval_runtime import RetrievalRuntime, RetrievalRuntimeConfig
from rag.services.rag_service import RAGService
from schemas.rag import RAGToolInputSchema
from schemas.status import ExecutionStatus


def _write_parent_jsonl(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "parent_chunk_id": "parent-1",
                "doc_id": "doc-1",
                "text": "真实父块正文",
                "child_chunk_ids": ["child-1"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


class _ParentStoreSmokeEngine:
    """Minimal engine that exercises the production resource-pool boundary."""

    def __init__(self, pool: ParentChildResourcePool) -> None:
        self.pool = pool

    def run(
        self,
        *,
        query: str,
        expected_parent_chunk_ids: list[str],
        **_: Any,
    ) -> dict[str, Any]:
        parent_chunk_id = (
            expected_parent_chunk_ids[0]
            if expected_parent_chunk_ids
            else "parent-1"
        )
        parent = self.pool.get_parent_store().get(parent_chunk_id)
        return {"query": query, "parent": parent}

    def close(self) -> None:
        self.pool.close()


class _ParentStoreSmokeFactory:
    def build(
        self,
        config: RetrievalRuntimeConfig,
        project_root: Path,
    ) -> tuple[_ParentStoreSmokeEngine, RetrievalRuntimeConfig]:
        pool = ParentChildResourcePool(
            runtime_config=config,
            project_root=project_root,
        )
        return _ParentStoreSmokeEngine(pool), config


@pytest.mark.unit
def test_parent_store_loads_real_jsonl_caches_closes_and_recreates(
    tmp_path: Path,
) -> None:
    parent_file = tmp_path / "parent_chunks.jsonl"
    _write_parent_jsonl(parent_file)
    pool = ParentChildResourcePool(
        runtime_config=SimpleNamespace(parent_file=str(parent_file)),
        project_root=tmp_path,
    )

    first = pool.get_parent_store()
    second = pool.get_parent_store()

    assert first is second
    assert first.get("parent-1")["text"] == "真实父块正文"
    assert first.get("parent-1")["parent_chunk_id"] == "parent-1"

    pool.close()

    assert pool._parent_store is None
    third = pool.get_parent_store()
    assert third is not first
    assert third.get("parent-1")["doc_id"] == "doc-1"


@pytest.mark.component_integration
def test_retrieval_runtime_smoke_reaches_real_parent_store_loading(
    tmp_path: Path,
) -> None:
    parent_file = tmp_path / "parent_chunks.jsonl"
    _write_parent_jsonl(parent_file)
    runtime = RetrievalRuntime(
        RetrievalRuntimeConfig(
            parent_file=str(parent_file),
            index_source_mode="legacy",
        ),
        project_root=tmp_path,
        runtime_factory=_ParentStoreSmokeFactory(),
    )

    result = runtime.retrieve(
        {
            "query": "测试真实父块加载",
            "expected_parent_chunk_ids": ["parent-1"],
        }
    )

    assert result["parent"]["parent_chunk_id"] == "parent-1"
    assert result["parent"]["text"] == "真实父块正文"
    runtime.close()


@pytest.mark.component_integration
def test_missing_parent_file_returns_structured_rag_failure(
    tmp_path: Path,
) -> None:
    missing_parent_file = tmp_path / "missing-parent-chunks.jsonl"
    runtime = RetrievalRuntime(
        RetrievalRuntimeConfig(
            parent_file=str(missing_parent_file),
            index_source_mode="legacy",
        ),
        project_root=tmp_path,
        runtime_factory=_ParentStoreSmokeFactory(),
    )
    service = RAGService(
        tmp_path,
        retrieval_runtime=runtime,
        allow_legacy_unscoped=True,
    )

    result = service.retrieve(
        RAGToolInputSchema(
            task_id="task-p0-parent-store-missing",
            run_id="run-p0-parent-store-missing",
            agent_name="P0RealRAGSmoke",
            query="触发首次父块加载",
        )
    )

    assert result.status == ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.error_code == "RAG_RETRIEVAL_FAILED"
    assert result.error.error_type == "FileNotFoundError"
    assert str(missing_parent_file) in result.error.message
    assert runtime.engine is not None
    assert runtime.engine.pool._parent_store is None
    service.close()
