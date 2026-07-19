"""Retrieval runtime lifecycle and active-index hot reload support."""

from __future__ import annotations

import copy
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from rag.common.coercion import split_csv
from rag.runtime.parent_child_runtime_factory import ParentChildRuntimeFactory

try:
    from rag.configs.RAGConfig import (
        CHILD_CHUNKS_FILE,
        EMBEDDING_BATCH_SIZE,
        EMBEDDING_DEVICE,
        EMBEDDING_MODEL_NAME,
        PARENT_CHILD_EVAL_TOP_K,
        PARENT_CHILD_MILVUS_COLLECTION_NAME,
        PARENT_CHILD_MILVUS_DB_FILE,
        PARENT_CHILD_RERANK_LOCAL_FILES_ONLY,
        PARENT_CHILD_RERANK_MAX_LENGTH,
        PARENT_CHILD_SEARCH_METRIC_TYPE,
        PARENT_CHUNKS_FILE,
        RERANKER_BATCH_SIZE,
        RERANKER_DEVICE,
        RERANKER_MODEL_NAME,
    )
except Exception:  # pragma: no cover - standalone defaults
    PARENT_CHUNKS_FILE = "data/processed/parent_child_chunks/parent_chunks.jsonl"
    CHILD_CHUNKS_FILE = "data/processed/parent_child_chunks/child_chunks.jsonl"
    PARENT_CHILD_MILVUS_DB_FILE = (
        "data/processed/vector_store/milvus_parent_child.db"
    )
    PARENT_CHILD_MILVUS_COLLECTION_NAME = "rag_child_chunks"
    PARENT_CHILD_SEARCH_METRIC_TYPE = "COSINE"
    EMBEDDING_MODEL_NAME = r"D:\models\huggingface\embedding\m3e-base"
    EMBEDDING_DEVICE = "cuda"
    EMBEDDING_BATCH_SIZE = 32
    RERANKER_MODEL_NAME = (
        r"D:\models\huggingface\reranker\bge-reranker-v2-m3"
    )
    RERANKER_DEVICE = "cuda"
    RERANKER_BATCH_SIZE = 16
    PARENT_CHILD_RERANK_MAX_LENGTH = 512
    PARENT_CHILD_RERANK_LOCAL_FILES_ONLY = True
    PARENT_CHILD_EVAL_TOP_K = 5

try:
    from rag.configs.LLMConfig import (
        QUERY_EXPANSION_DO_SAMPLE,
        QUERY_EXPANSION_LLM_DEVICE,
        QUERY_EXPANSION_LLM_ENABLED,
        QUERY_EXPANSION_LLM_MODEL_NAME,
        QUERY_EXPANSION_TEMPERATURE,
        QUERY_EXPANSION_TOP_P,
        QUERY_HYDE_MAX_NEW_TOKENS,
        QUERY_REWRITE_MAX_NEW_TOKENS,
    )
except Exception:  # pragma: no cover - standalone defaults
    QUERY_EXPANSION_LLM_ENABLED = True
    QUERY_EXPANSION_LLM_MODEL_NAME = (
        r"D:\models\huggingface\llm\Qwen2.5-1.5B-Instruct"
    )
    QUERY_EXPANSION_LLM_DEVICE = "cuda"
    QUERY_REWRITE_MAX_NEW_TOKENS = 192
    QUERY_HYDE_MAX_NEW_TOKENS = 256
    QUERY_EXPANSION_TEMPERATURE = 0.1
    QUERY_EXPANSION_TOP_P = 0.9
    QUERY_EXPANSION_DO_SAMPLE = False


@dataclass
class RetrievalRuntimeConfig:
    """Configuration used to build the parent-child retrieval runtime."""

    parent_file: str = str(PARENT_CHUNKS_FILE)
    child_file: str = str(CHILD_CHUNKS_FILE)
    db_file: str = str(PARENT_CHILD_MILVUS_DB_FILE)
    capture_output: str = "data/processed/runs/rag_runs.jsonl"

    use_active_index_manifest: bool = True
    active_index_pointer: str = "data/processed/indexes/active_index.json"
    verify_active_index_manifest_hash: bool = True
    verify_active_index_artifacts: bool = False
    index_version: str = "legacy_unversioned_index"
    dataset_version: str = "unknown"
    index_manifest_file: str = ""
    index_manifest_hash: str = ""
    index_config_hash: str = ""
    index_reproducibility_hash: str = ""
    index_lineage: dict[str, Any] | None = None

    static_retrieval_spec_file: str = "backend/rag/config/static_retrieval_v1.yaml"
    intent_policy_file: str = "backend/rag/config/intent_policy_v1.yaml"
    retrieval_gate_policy_file: str = (
        "backend/rag/config/retrieval_gate_policy_v1.yaml"
    )
    collection_name: str = str(PARENT_CHILD_MILVUS_COLLECTION_NAME)
    metric_type: str = str(PARENT_CHILD_SEARCH_METRIC_TYPE)

    embedding_model: str = str(EMBEDDING_MODEL_NAME)
    embedding_device: str = str(EMBEDDING_DEVICE)
    embedding_batch_size: int = int(EMBEDDING_BATCH_SIZE)
    hash_embedding: bool = False
    hash_dim: int = 768

    reranker_model: str = str(RERANKER_MODEL_NAME)
    reranker_device: str = str(RERANKER_DEVICE)
    reranker_batch_size: int = int(RERANKER_BATCH_SIZE)
    reranker_max_length: int = int(PARENT_CHILD_RERANK_MAX_LENGTH)
    reranker_local_files_only: bool = bool(
        PARENT_CHILD_RERANK_LOCAL_FILES_ONLY
    )
    eval_top_k: int = int(PARENT_CHILD_EVAL_TOP_K)

    # Query transformation is retrieval-side generation (Multi-Query/HyDE),
    # distinct from application answer generation.
    enable_query_expansion_llm: bool = bool(QUERY_EXPANSION_LLM_ENABLED)
    query_expansion_llm_model: str = str(QUERY_EXPANSION_LLM_MODEL_NAME)
    query_expansion_llm_device: str = str(QUERY_EXPANSION_LLM_DEVICE)
    query_rewrite_max_new_tokens: int = int(QUERY_REWRITE_MAX_NEW_TOKENS)
    query_hyde_max_new_tokens: int = int(QUERY_HYDE_MAX_NEW_TOKENS)
    query_expansion_temperature: float = float(QUERY_EXPANSION_TEMPERATURE)
    query_expansion_top_p: float = float(QUERY_EXPANSION_TOP_P)
    query_expansion_do_sample: bool = bool(QUERY_EXPANSION_DO_SAMPLE)

    pipeline_name: str = "parent_child_hybrid_retrieval"
    pipeline_version: str = "v1.0"


class RetrievalRuntime:
    """Own one engine and swap it safely when the active index changes."""

    def __init__(
        self,
        config: RetrievalRuntimeConfig | None = None,
        *,
        project_root: str | Path | None = None,
        runtime_factory: ParentChildRuntimeFactory | None = None,
    ) -> None:
        self._base_config = copy.deepcopy(config or RetrievalRuntimeConfig())
        self.config = copy.deepcopy(self._base_config)
        self.project_root = (
            Path(project_root).resolve() if project_root else Path.cwd().resolve()
        )
        self.runtime_factory = runtime_factory or ParentChildRuntimeFactory()
        self.engine: Any | None = None
        self._initialized = False
        self._active_pointer_hash: str | None = None
        self._condition = threading.Condition(threading.RLock())
        self._inflight_requests = 0
        self._reloading = False

    @classmethod
    def from_default_config(
        cls, *, project_root: str | Path | None = None
    ) -> "RetrievalRuntime":
        return cls(RetrievalRuntimeConfig(), project_root=project_root)

    def _pointer_hash(self) -> str | None:
        if not self._base_config.use_active_index_manifest:
            return None
        from rag.offline.manifest import sha256_file

        pointer = Path(self._base_config.active_index_pointer)
        pointer = (
            (self.project_root / pointer).resolve()
            if not pointer.is_absolute()
            else pointer.expanduser().resolve()
        )
        return sha256_file(pointer) if pointer.is_file() else None

    @staticmethod
    def _close_engine(engine: Any) -> None:
        close = getattr(engine, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    def initialize(self) -> None:
        with self._condition:
            while self._reloading:
                self._condition.wait()
            if self._initialized and self.engine is not None:
                return
            self._reloading = True

        try:
            new_engine, new_config = self.runtime_factory.build(
                copy.deepcopy(self._base_config), self.project_root
            )
            pointer_hash = self._pointer_hash()
        except Exception:
            with self._condition:
                self._reloading = False
                self._condition.notify_all()
            raise

        with self._condition:
            self.engine = new_engine
            self.config = new_config
            self._active_pointer_hash = pointer_hash
            self._initialized = True
            self._reloading = False
            self._condition.notify_all()

    def active_index_changed(self) -> bool:
        return self._pointer_hash() != self._active_pointer_hash

    def reload_active_index(
        self, *, force: bool = False, timeout_seconds: float = 60.0
    ) -> dict[str, Any]:
        """Rebuild and atomically swap the engine after a pointer change."""
        self.initialize()
        target_pointer_hash = self._pointer_hash()
        with self._condition:
            if not force and target_pointer_hash == self._active_pointer_hash:
                return {
                    "status": "unchanged",
                    "index_version": getattr(self.config, "index_version", None),
                    "pointer_hash": self._active_pointer_hash,
                }
            while self._reloading:
                self._condition.wait()
            self._reloading = True
            deadline = time.monotonic() + max(0.1, float(timeout_seconds))
            while self._inflight_requests > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._reloading = False
                    self._condition.notify_all()
                    raise TimeoutError(
                        "timed out waiting for retrieval requests before reload"
                    )
                self._condition.wait(timeout=remaining)
            old_engine = self.engine
            old_version = getattr(self.config, "index_version", None)

        try:
            new_engine, new_config = self.runtime_factory.build(
                copy.deepcopy(self._base_config), self.project_root
            )
            resolved_pointer_hash = self._pointer_hash()
            if resolved_pointer_hash != target_pointer_hash:
                self._close_engine(new_engine)
                raise RuntimeError(
                    "active index pointer changed during rebuild; retry reload"
                )
        except Exception:
            with self._condition:
                self._reloading = False
                self._condition.notify_all()
            raise

        with self._condition:
            self.engine = new_engine
            self.config = new_config
            self._active_pointer_hash = resolved_pointer_hash
            self._initialized = True

        self._close_engine(old_engine)
        with self._condition:
            self._reloading = False
            self._condition.notify_all()

        return {
            "status": "reloaded",
            "previous_index_version": old_version,
            "index_version": getattr(new_config, "index_version", None),
            "pointer_hash": resolved_pointer_hash,
        }

    def close(self, *, timeout_seconds: float = 60.0) -> None:
        with self._condition:
            while self._reloading:
                self._condition.wait()
            self._reloading = True
            deadline = time.monotonic() + max(0.1, float(timeout_seconds))
            while self._inflight_requests > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._reloading = False
                    self._condition.notify_all()
                    raise TimeoutError(
                        "timed out waiting for retrieval requests before close"
                    )
                self._condition.wait(timeout=remaining)
            old_engine = self.engine
            self.engine = None
            self._initialized = False
            self._active_pointer_hash = None

        self._close_engine(old_engine)
        with self._condition:
            self._reloading = False
            self._condition.notify_all()

    @contextmanager
    def _runtime_session(
        self,
    ) -> Iterator[tuple[Any, RetrievalRuntimeConfig]]:
        self.initialize()
        with self._condition:
            while self._reloading:
                self._condition.wait()
            if self.engine is None:
                raise RuntimeError("retrieval engine is not initialized")
            engine = self.engine
            config = self.config
            self._inflight_requests += 1
        try:
            yield engine, config
        finally:
            with self._condition:
                self._inflight_requests -= 1
                self._condition.notify_all()

    def retrieve(self, request: dict[str, Any]) -> dict[str, Any]:
        """Execute retrieval against the current active engine."""
        query = str(request.get("query") or "").strip()
        if not query:
            raise ValueError("request['query'] cannot be empty")
        request_context = dict(request.get("extra_metadata") or {})
        with self._runtime_session() as (engine, config):
            return engine.run(
                query=query,
                eval_top_k=int(request.get("eval_top_k", config.eval_top_k)),
                expected_doc_ids=split_csv(request.get("expected_doc_ids")),
                expected_parent_chunk_ids=split_csv(
                    request.get("expected_parent_chunk_ids")
                ),
                expected_child_chunk_ids=split_csv(
                    request.get("expected_child_chunk_ids")
                ),
                expected_keywords=split_csv(request.get("expected_keywords")),
                filter_expr=str(request.get("filter_expr") or "").strip() or None,
                keyword_doc_ids=split_csv(request.get("keyword_doc_ids")),
                extra_metadata={
                    **request_context,
                    "request_context": request_context,
                    "offline_index": dict(config.index_lineage or {}),
                    "static_retrieval_spec": {
                        "path": str(config.static_retrieval_spec_file),
                        "schema_version": getattr(
                            config, "static_retrieval_spec_schema_version", None
                        ),
                        "spec_id": getattr(
                            config, "static_retrieval_spec_id", None
                        ),
                        "spec_version": getattr(
                            config, "static_retrieval_spec_version", None
                        ),
                        "hash": getattr(
                            config, "static_retrieval_spec_hash", None
                        ),
                        "components": getattr(
                            config, "static_retrieval_component_metadata", {}
                        ),
                    },
                    "intent_policy_id": getattr(config, "intent_policy_id", None),
                    "retrieval_gate_policy_id": getattr(
                        config, "retrieval_gate_policy_id", None
                    ),
                },
            )
